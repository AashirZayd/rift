"""Build the dependency graph from per-file analysis results (SPEC §10, ADR-0009).

Nodes:
- one ``file:`` node per result;
- one node per module, class, function and method symbol, with the matching :class:`NodeKind`;
- test functions and methods become ``test:<path>::<Class>::<name>`` nodes (pytest node IDs,
  without parameters). A test-kind symbol nested in a function (``<locals>`` in its qualname)
  cannot be collected by pytest, so it stays a ``py:`` method or function node;
- constants have no :class:`NodeKind` in v0.1. They fold into their module node, and an edge to
  ``py:m:CONST`` becomes an edge to ``py:m``. That is coarser, but every dependant of the constant
  is still a dependant of its module.

Edges keep their kind, origin, confidence, flags and evidence IDs; only endpoints renamed above
change. An edge whose target is not a node is never dropped. It becomes an
:class:`UnresolvedReference` from the edge's source: ``missing_module`` when the target's module has
no node at all, else ``unknown``, with the target ID as ``raw``. The analyzer's own unresolved
references are carried over, with the same renaming.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.frozen import FrozenMap
from rift.core.ids import NodeId, file_node_id, module_node_id, node_id_for_test
from rift.core.models.analysis import AnalysisResult, sort_unique, unresolved_sort_key
from rift.core.models.graph import Node, NodeKind, UnresolvedReason, UnresolvedReference
from rift.core.models.source import SourceLocation, Symbol, SymbolKind
from rift.graph.errors import GraphError
from rift.graph.multigraph import DependencyGraph

_NODE_KINDS: Mapping[SymbolKind, NodeKind] = MappingProxyType(
    {
        SymbolKind.MODULE: NodeKind.MODULE,
        SymbolKind.CLASS: NodeKind.CLASS,
        SymbolKind.FUNCTION: NodeKind.FUNCTION,
        SymbolKind.METHOD: NodeKind.METHOD,
        SymbolKind.TEST_FUNCTION: NodeKind.FUNCTION,  # unless it becomes a test node
        SymbolKind.TEST_METHOD: NodeKind.METHOD,  # unless it becomes a test node
    }
)
_TEST_KINDS = frozenset({SymbolKind.TEST_FUNCTION, SymbolKind.TEST_METHOD})


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphBuild:
    """The graph plus every unresolved reference, sorted by ``unresolved_sort_key``."""

    graph: DependencyGraph
    unresolved: tuple[UnresolvedReference, ...]


def pytest_node_of(symbol: Symbol) -> NodeId | None:
    """The pytest node ID of a test symbol, or ``None`` if pytest could not collect it."""
    if symbol.kind not in _TEST_KINDS or "<" in symbol.qualname:
        return None
    return node_id_for_test(f"{symbol.location.path}::{symbol.qualname.replace('.', '::')}")


def _symbol_node(symbol: Symbol) -> Node:
    attributes = FrozenMap.of(
        {"symbol_kind": symbol.kind.value, "visibility": symbol.visibility.value}
    )
    test_id = pytest_node_of(symbol)
    if test_id is not None:
        return Node(
            id=test_id,
            kind=NodeKind.TEST,
            label=test_id.removeprefix("test:"),
            location=symbol.location,
            attributes=attributes,
        )
    return Node(
        id=NodeId(symbol.id),
        kind=_NODE_KINDS[symbol.kind],
        label=symbol.module if symbol.kind is SymbolKind.MODULE else symbol.qualname,
        location=symbol.location,
        attributes=attributes,
    )


def _missing_reason(graph: DependencyGraph, target: NodeId) -> UnresolvedReason:
    prefix, _, rest = target.partition(":")
    if prefix == "py":
        module = rest.partition(":")[0]
        if not graph.has_node(module_node_id(module)):
            return UnresolvedReason.MISSING_MODULE
    return UnresolvedReason.UNKNOWN


def build_graph(results: Iterable[AnalysisResult]) -> GraphBuild:
    """Turn analysis results into a :class:`DependencyGraph` (deterministic in any input order).

    Raises :class:`GraphError` if two results describe the same file or define one node
    differently: that is a producer bug, never something to paper over.
    """
    ordered = sorted(results, key=lambda result: result.file_path)
    graph = DependencyGraph()
    renamed: dict[NodeId, NodeId] = {}
    seen_paths: set[str] = set()
    for result in ordered:
        if result.file_path in seen_paths:
            raise GraphError(
                f"two analysis results for {result.file_path!r}", code=ErrorCode.GRAPH_CONFLICT
            )
        seen_paths.add(result.file_path)
        graph.add_node(
            Node(
                id=file_node_id(result.file_path),
                kind=NodeKind.FILE,
                label=result.file_path,
                location=None,
            )
        )
        for symbol in result.symbols:
            if symbol.kind is SymbolKind.CONSTANT:
                renamed[NodeId(symbol.id)] = module_node_id(symbol.module)
                continue
            node = _symbol_node(symbol)
            graph.add_node(node)
            if node.id != str(symbol.id):
                renamed[NodeId(symbol.id)] = node.id

    unresolved: list[UnresolvedReference] = []
    for result in ordered:
        for edge in result.edges:
            source = renamed.get(edge.source, edge.source)
            target = renamed.get(edge.target, edge.target)
            if graph.has_node(target):
                graph.add_edge(replace(edge, source=source, target=target))
                continue
            unresolved.append(
                UnresolvedReference(
                    source=source,
                    raw=target,
                    reason=_missing_reason(graph, target),
                    location=_anchor(graph, source, result.file_path),
                )
            )
        for ref in result.unresolved:
            source = renamed.get(ref.source, ref.source)
            unresolved.append(ref if source == ref.source else replace(ref, source=source))
    return GraphBuild(graph=graph, unresolved=sort_unique(unresolved, unresolved_sort_key))


def _anchor(graph: DependencyGraph, source: NodeId, path: str) -> SourceLocation:
    """Where an unresolved edge is reported: its source's location, else the top of the file."""
    location = graph.node(source).location
    if location is not None:
        return location
    return SourceLocation(path=path, start_line=1, start_col=0, end_line=1, end_col=0)
