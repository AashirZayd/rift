"""SPEC §10: traversal is iterative. Deep chains and cycle-heavy graphs terminate without
recursion, even with the interpreter recursion limit pinned just above the current stack."""

import ast
import sys
import textwrap
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

import pytest

import rift.graph
from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, Edge, EdgeKind, EdgeOrigin, Node, NodeKind
from rift.graph.multigraph import DependencyGraph
from rift.graph.traversal import breadth_first

CHAIN = 100_000
KINDS = tuple(EdgeKind)


def _name(i: int) -> NodeId:
    return NodeId(f"py:n{i:06d}")


def _graph(size: int, pairs: Iterator[tuple[int, int, EdgeKind]]) -> DependencyGraph:
    graph = DependencyGraph()
    for i in range(size):
        graph.add_node(Node(id=_name(i), kind=NodeKind.MODULE, label=f"n{i}", location=None))
    for source, target, kind in pairs:
        graph.add_edge(
            Edge(
                source=_name(source),
                target=_name(target),
                kind=kind,
                origin=EdgeOrigin.NAME_RESOLUTION,
                confidence=Confidence.CERTAIN,
                evidence_ids=(),
                flags=frozenset(),
            )
        )
    return graph


def _stack_depth() -> int:
    depth = 0
    frame: FrameType | None = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


@contextmanager
def tight_recursion_limit(headroom: int = 40) -> Iterator[None]:
    """Any recursion deeper than ``headroom`` frames below the caller raises RecursionError."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(_stack_depth() + headroom)
    try:
        yield
    finally:
        sys.setrecursionlimit(old)


def _recursive_depth(graph: DependencyGraph, node: NodeId) -> int:
    """Positive control: a naive recursive walk, which the tight limit must break."""
    nxt = graph.successors(node)
    return 1 + (_recursive_depth(graph, nxt[0].target) if nxt else 0)


@pytest.fixture(scope="module")
def chain() -> DependencyGraph:
    return _graph(CHAIN, ((i, i + 1, EdgeKind.IMPORTS) for i in range(CHAIN - 1)))


@pytest.mark.slow
def test_100k_chain_forward_and_reverse_without_recursion(chain: DependencyGraph) -> None:
    last = _name(CHAIN - 1)
    with tight_recursion_limit():
        forward = chain.forward_reachable(_name(0))
        reverse = chain.reverse_reachable(last)
        path = forward.path(last)
        edges = reverse.path_edges(_name(0))
    assert len(forward.nodes) == len(reverse.nodes) == CHAIN
    assert forward.depth(last) == CHAIN - 1
    assert path[0] == _name(0)
    assert path[-1] == last
    assert len(path) == CHAIN
    assert len(edges) == CHAIN - 1
    assert edges[0].target == last


@pytest.mark.slow
def test_tight_limit_really_breaks_recursion(chain: DependencyGraph) -> None:
    with tight_recursion_limit(), pytest.raises(RecursionError):
        _recursive_depth(chain, _name(0))


@pytest.mark.slow
def test_100k_chain_respects_max_depth(chain: DependencyGraph) -> None:
    with tight_recursion_limit():
        result = chain.forward_reachable(_name(0), max_depth=50_000)
    assert len(result.nodes) == 50_001


def _cycle_heavy_pairs(size: int, clique: int) -> Iterator[tuple[int, int, EdgeKind]]:
    for i in range(size):
        yield i, (i + 1) % size, KINDS[i % len(KINDS)]
        yield i, (i * 7 + 3) % size, EdgeKind.CALLS
        yield i, i // 2, EdgeKind.REFERENCES
        yield i, (i + size // 2) % size, EdgeKind.IMPORTS
    for a in range(clique):
        for b in range(clique):
            yield a, b, EdgeKind.CALLS  # includes self-loops


@pytest.mark.slow
def test_cycle_heavy_graph_terminates_with_bfs_layering() -> None:
    size, clique = 20_000, 200
    graph = _graph(size, _cycle_heavy_pairs(size, clique))
    assert graph.edge_count > 100_000
    with tight_recursion_limit():
        forward = graph.forward_reachable(_name(size - 1))
        reverse = graph.reverse_reachable(_name(0))
    assert len(forward.nodes) == len(reverse.nodes) == size
    for result in (forward, reverse):
        for node in result.nodes:
            step = result.steps[node]
            if step is not None:  # BFS layering: a parent is exactly one hop closer
                assert result.depth(node) == result.depth(step.previous) + 1


class CountingAdjacency(Mapping[NodeId, Mapping[EdgeKind, Mapping[NodeId, Edge]]]):
    """Counts how often each node's neighbours are fetched, i.e. how often it is expanded."""

    def __init__(self, inner: Mapping[NodeId, Mapping[EdgeKind, Mapping[NodeId, Edge]]]) -> None:
        self.inner = inner
        self.expanded: Counter[NodeId] = Counter()

    def __getitem__(self, key: NodeId) -> Mapping[EdgeKind, Mapping[NodeId, Edge]]:
        self.expanded[key] += 1
        return self.inner[key]

    def __iter__(self) -> Iterator[NodeId]:
        return iter(self.inner)

    def __len__(self) -> int:
        return len(self.inner)

    def __contains__(self, key: object) -> bool:
        return key in self.inner


@pytest.mark.slow
def test_cycle_heavy_traversal_expands_every_node_exactly_once() -> None:
    size, clique = 20_000, 200
    adjacency: dict[NodeId, dict[EdgeKind, dict[NodeId, Edge]]] = {
        _name(i): {} for i in range(size)
    }
    for source, target, kind in _cycle_heavy_pairs(size, clique):
        adjacency[_name(source)].setdefault(kind, {})[_name(target)] = Edge(
            source=_name(source),
            target=_name(target),
            kind=kind,
            origin=EdgeOrigin.NAME_RESOLUTION,
            confidence=Confidence.CERTAIN,
            evidence_ids=(),
            flags=frozenset(),
        )
    counting = CountingAdjacency(adjacency)
    with tight_recursion_limit():
        result = breadth_first(
            counting, _name(0), kinds=None, min_confidence=Confidence.UNKNOWN, max_depth=None
        )
    assert len(result.nodes) == size
    assert set(counting.expanded.values()) == {1}
    assert sum(counting.expanded.values()) == size


def _functions(tree: ast.Module) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """Every function with whether it is a method (defined directly in a class body)."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                yield child, isinstance(parent, ast.ClassDef)


def _calls_itself(func: ast.FunctionDef | ast.AsyncFunctionDef, *, method: bool) -> bool:
    for call in ast.walk(func):
        if not isinstance(call, ast.Call):
            continue
        callee = call.func
        if method:  # a bare name inside a method refers to a module-level function
            if (
                isinstance(callee, ast.Attribute)
                and callee.attr == func.name
                and isinstance(callee.value, ast.Name)
                and callee.value.id in {"self", "cls"}
            ):
                return True
        elif isinstance(callee, ast.Name) and callee.id == func.name:
            return True
    return False


def test_no_function_in_rift_graph_calls_itself() -> None:
    """Static guard: no direct recursion anywhere in rift.graph. Mutual recursion is not
    detected; the iterative-only rule is also checked behaviourally by the deep-chain tests."""
    offenders = [
        f"{path.name}:{func.name}"
        for path in sorted(Path(rift.graph.__file__).parent.glob("*.py"))
        for func, method in _functions(ast.parse(path.read_text(encoding="utf-8")))
        if _calls_itself(func, method=method)
    ]
    assert offenders == []


def test_recursion_guard_detects_recursion() -> None:
    """Positive control for the static guard."""
    tree = ast.parse(
        textwrap.dedent(
            """
            def f(n):
                return f(n - 1)
            class C:
                def g(self):
                    return self.g()
                def h(self):
                    return h()
            """
        )
    )
    found = {func.name: _calls_itself(func, method=method) for func, method in _functions(tree)}
    assert found == {"f": True, "g": True, "h": False}
