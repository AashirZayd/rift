"""SPEC §10: graph construction, queries, traversal filters, explanation paths, serialization."""

import json
from collections.abc import Callable

import pytest

from rift.core.canonical import canonical_json
from rift.core.errors import ErrorCode, SerializationError
from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, EdgeKind, Node, NodeKind
from rift.graph.errors import GraphError
from rift.graph.multigraph import DependencyGraph
from tests.unit.graph.helpers import edge, graph_of, nid

CALLS = EdgeKind.CALLS
IMPORTS = EdgeKind.IMPORTS


def ids(*names: str) -> tuple[NodeId, ...]:
    return tuple(nid(name) for name in names)


# ---------------------------------------------------------------------- construction


def test_add_node_is_idempotent_for_equal_nodes() -> None:
    graph = graph_of(["a", "a"])
    assert graph.node_count == 1


def test_add_node_rejects_a_different_node_under_the_same_id() -> None:
    graph = graph_of(["a"])
    with pytest.raises(GraphError) as exc_info:
        graph.add_node(Node(id=nid("a"), kind=NodeKind.MODULE, label="other", location=None))
    assert exc_info.value.code is ErrorCode.GRAPH_CONFLICT


@pytest.mark.parametrize(("source", "target"), [("a", "zz"), ("zz", "a")])
def test_add_edge_rejects_an_edge_with_a_missing_endpoint(source: str, target: str) -> None:
    graph = graph_of(["a"])
    with pytest.raises(GraphError) as exc_info:
        graph.add_edge(edge(source, target))
    assert exc_info.value.code is ErrorCode.UNKNOWN_NODE
    assert graph.edge_count == 0


def test_unknown_node_lookups_raise() -> None:
    graph = graph_of(["a"])
    for call in (
        lambda: graph.node(nid("zz")),
        lambda: graph.successors(nid("zz")),
        lambda: graph.predecessors(nid("zz")),
        lambda: graph.forward_reachable(nid("zz")),
        lambda: graph.reverse_reachable([nid("a"), nid("zz")]),
        lambda: graph.subgraph([nid("zz")]),
    ):
        with pytest.raises(GraphError):
            call()


def test_nodes_and_edges_are_sorted_whatever_the_insertion_order() -> None:
    graph = graph_of(["c", "a", "b"], [edge("c", "a"), edge("a", "c"), edge("a", "b", kind=CALLS)])
    assert [n.id for n in graph.nodes()] == list(ids("a", "b", "c"))
    assert [(e.source, e.kind, e.target) for e in graph.edges()] == [
        (nid("a"), CALLS, nid("b")),
        (nid("a"), IMPORTS, nid("c")),
        (nid("c"), IMPORTS, nid("a")),
    ]


def test_successors_and_predecessors_filter_and_sort() -> None:
    graph = graph_of(
        ["a", "b", "c"],
        [
            edge("a", "c", confidence=Confidence.POSSIBLE),
            edge("a", "b", kind=CALLS),
            edge("a", "b"),
            edge("c", "b", kind=CALLS, confidence=Confidence.LIKELY),
        ],
    )
    out = graph.successors(nid("a"))
    assert [(e.target, e.kind) for e in out] == [
        (nid("b"), CALLS),
        (nid("b"), IMPORTS),
        (nid("c"), IMPORTS),
    ]
    assert graph.successors(nid("a"), kinds={IMPORTS}, min_confidence=Confidence.LIKELY) == (
        graph.edge(nid("a"), IMPORTS, nid("b")),
    )
    assert [e.source for e in graph.predecessors(nid("b"), kinds={CALLS})] == list(ids("a", "c"))
    assert graph.predecessors(nid("b"), kinds=()) == ()


def test_graph_is_unhashable_and_compares_by_content() -> None:
    first = graph_of(["a", "b"], [edge("a", "b")])
    second = graph_of(["b", "a"], [edge("a", "b")])
    assert first == second
    assert first != graph_of(["a", "b"])
    assert first != "graph"
    with pytest.raises(TypeError):
        hash(first)
    assert repr(first) == "DependencyGraph(nodes=2, edges=1)"


# ---------------------------------------------------------------------- traversal


@pytest.fixture
def layered() -> DependencyGraph:
    """a -> b -> d, a -> c -> d (diamond), d -> e (possible), e -> a (cycle, calls)."""
    return graph_of(
        ["a", "b", "c", "d", "e", "lonely"],
        [
            edge("a", "c"),
            edge("a", "b"),
            edge("c", "d"),
            edge("b", "d"),
            edge("d", "e", confidence=Confidence.POSSIBLE),
            edge("e", "a", kind=CALLS),
        ],
    )


def test_forward_reachable_includes_starts_and_is_sorted(layered: DependencyGraph) -> None:
    result = layered.forward_reachable(nid("a"))
    assert result.nodes == ids("a", "b", "c", "d", "e")
    assert result.starts == ids("a")
    assert result.depth(nid("a")) == 0
    assert result.path(nid("a")) == ids("a")
    assert result.path_edges(nid("a")) == ()
    assert nid("lonely") not in result


def test_shortest_path_ties_break_to_the_smallest_ids(layered: DependencyGraph) -> None:
    result = layered.forward_reachable(nid("a"))
    assert result.path(nid("d")) == ids("a", "b", "d")  # not a -> c -> d
    assert result.depth(nid("e")) == 3
    assert [(e.source, e.target) for e in result.path_edges(nid("e"))] == [
        (nid("a"), nid("b")),
        (nid("b"), nid("d")),
        (nid("d"), nid("e")),
    ]


def test_reverse_reachable_walks_edges_backwards(layered: DependencyGraph) -> None:
    result = layered.reverse_reachable(nid("d"))
    assert result.nodes == ids("a", "b", "c", "d", "e")
    assert result.path(nid("a")) == ids("d", "b", "a")
    # In reverse, edge i's target is path[i].
    assert [(e.source, e.target) for e in result.path_edges(nid("a"))] == [
        (nid("b"), nid("d")),
        (nid("a"), nid("b")),
    ]
    assert result.path(nid("e")) == ids("d", "b", "a", "e")


def test_kind_filter(layered: DependencyGraph) -> None:
    assert layered.reverse_reachable(nid("d"), kinds={IMPORTS}).nodes == ids("a", "b", "c", "d")
    assert layered.forward_reachable(nid("a"), kinds=()).nodes == ids("a")


def test_min_confidence_filter(layered: DependencyGraph) -> None:
    result = layered.forward_reachable(nid("a"), min_confidence=Confidence.LIKELY)
    assert result.nodes == ids("a", "b", "c", "d")


def test_max_depth(layered: DependencyGraph) -> None:
    assert layered.forward_reachable(nid("a"), max_depth=0).nodes == ids("a")
    assert layered.forward_reachable(nid("a"), max_depth=1).nodes == ids("a", "b", "c")
    assert layered.forward_reachable(nid("a"), max_depth=2).nodes == ids("a", "b", "c", "d")


@pytest.mark.parametrize("bad", [-1, True])
def test_invalid_max_depth_is_rejected(layered: DependencyGraph, bad: int) -> None:
    with pytest.raises(GraphError) as exc_info:
        layered.forward_reachable(nid("a"), max_depth=bad)
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


def test_invalid_min_confidence_is_rejected(layered: DependencyGraph) -> None:
    with pytest.raises(GraphError):
        layered.forward_reachable(nid("a"), min_confidence="certain")  # type: ignore[arg-type]  # deliberately wrong


def test_several_starts_share_one_traversal(layered: DependencyGraph) -> None:
    result = layered.forward_reachable([nid("d"), nid("lonely"), nid("d")])
    assert result.starts == ids("d", "lonely")
    assert result.nodes == ids("a", "b", "c", "d", "e", "lonely")
    assert result.path(nid("b")) == ids("d", "e", "a", "b")


def test_path_to_an_unreached_node_raises(layered: DependencyGraph) -> None:
    result = layered.forward_reachable(nid("b"), kinds={IMPORTS})
    for call in (result.path, result.path_edges, result.depth):
        with pytest.raises(GraphError):
            call(nid("lonely"))


def test_parallel_edges_of_different_kinds_explain_with_the_strongest() -> None:
    graph = graph_of(
        ["a", "b"],
        [
            edge("a", "b", kind=CALLS, confidence=Confidence.POSSIBLE),
            edge("a", "b", kind=EdgeKind.REFERENCES, confidence=Confidence.CERTAIN),
            edge("a", "b", kind=IMPORTS, confidence=Confidence.CERTAIN),
        ],
    )
    (step,) = graph.forward_reachable(nid("a")).path_edges(nid("b"))
    assert (step.kind, step.confidence) == (IMPORTS, Confidence.CERTAIN)  # tie: smaller kind
    (step,) = graph.forward_reachable(nid("a"), kinds={CALLS}).path_edges(nid("b"))
    assert step.kind is CALLS


def test_self_loops_terminate() -> None:
    graph = graph_of(["a", "b"], [edge("a", "a"), edge("a", "b"), edge("b", "b")])
    assert graph.forward_reachable(nid("a")).nodes == ids("a", "b")


# ---------------------------------------------------------------------- subgraph


def test_subgraph_keeps_only_edges_inside_the_selection(layered: DependencyGraph) -> None:
    sub = layered.subgraph([nid("a"), nid("b"), nid("d")])
    assert [n.id for n in sub.nodes()] == list(ids("a", "b", "d"))
    assert [(e.source, e.target) for e in sub.edges()] == [
        (nid("a"), nid("b")),
        (nid("b"), nid("d")),
    ]


# ---------------------------------------------------------------------- serialization


def test_round_trip_and_canonical_form(layered: DependencyGraph) -> None:
    data = layered.to_dict()
    assert data["format"] == "rift.graph"
    assert data["version"] == 1
    again = DependencyGraph.from_dict(json.loads(canonical_json(data)))
    assert again == layered
    assert canonical_json(again.to_dict()) == canonical_json(data)


_BASE = graph_of(["a", "b"], [edge("a", "b")]).to_dict()
_NODES = list(_BASE["nodes"]) if isinstance(_BASE["nodes"], list) else []
_EDGES = list(_BASE["edges"]) if isinstance(_BASE["edges"], list) else []
_NODE_A = _NODES[0] if isinstance(_NODES[0], dict) else {}


def _document(**overrides: object) -> dict[str, object]:
    return {**_BASE, **overrides}


@pytest.mark.parametrize(
    "document",
    [
        pytest.param([], id="not-an-object"),
        pytest.param(_document(extra=1), id="unknown-key"),
        pytest.param({k: v for k, v in _BASE.items() if k != "edges"}, id="missing-key"),
        pytest.param(_document(format="other"), id="wrong-format"),
        pytest.param(_document(version=True), id="bool-version"),
        pytest.param(_document(version=2), id="future-version"),
        pytest.param(_document(nodes={}), id="nodes-not-a-list"),
        pytest.param(_document(nodes=[{"id": "py:a"}]), id="bad-node"),
        pytest.param(_document(nodes=[{**_NODE_A, "kind": "nonsense"}, _NODES[1]]), id="bad-kind"),
        pytest.param(_document(nodes=[*_NODES, _NODES[0]]), id="duplicate-node"),
        pytest.param(_document(nodes=[_NODES[0]]), id="dangling-edge"),
        pytest.param(_document(edges=[*_EDGES, _EDGES[0]]), id="duplicate-edge"),
    ],
)
def test_from_dict_is_strict(document: object) -> None:
    with pytest.raises(SerializationError):
        DependencyGraph.from_dict(document)


def test_explanation_prefers_the_path_with_fewer_weak_edges() -> None:
    """Review finding #5: a certain route beats a lexicographically smaller weak one."""
    graph = graph_of(
        ["a", "b", "c", "d"],
        [
            edge("a", "b", confidence=Confidence.POSSIBLE),
            edge("b", "d"),
            edge("a", "c"),
            edge("c", "d", confidence=Confidence.LIKELY),
        ],
    )
    assert graph.forward_reachable(nid("a")).path(nid("d")) == ids("a", "c", "d")
    assert graph.reverse_reachable(nid("d")).path(nid("a")) == ids("d", "c", "a")
    # Without the weak link the smaller IDs decide again.
    strong = graph_of(
        ["a", "b", "c", "d"], [edge("a", "b"), edge("b", "d"), edge("a", "c"), edge("c", "d")]
    )
    assert strong.forward_reachable(nid("a")).path(nid("d")) == ids("a", "b", "d")


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"kinds": CALLS}, id="bare-enum-kinds"),
        pytest.param({"kinds": "calls"}, id="string-kinds"),
        pytest.param({"kinds": ["calls"]}, id="non-enum-kind"),
        pytest.param({"kinds": 3}, id="non-iterable-kinds"),
        pytest.param({"min_confidence": "likely"}, id="string-confidence"),
        pytest.param({"max_depth": 0.5}, id="float-depth"),
        pytest.param({"max_depth": "1"}, id="string-depth"),
    ],
)
def test_traversal_and_queries_reject_malformed_filters(
    layered: DependencyGraph, arguments: dict[str, object]
) -> None:
    query_arguments = {k: v for k, v in arguments.items() if k != "max_depth"}
    calls: list[Callable[[], object]] = [
        lambda: layered.forward_reachable(nid("a"), **arguments),  # type: ignore[arg-type]  # deliberately malformed
        lambda: layered.reverse_reachable(nid("a"), **arguments),  # type: ignore[arg-type]  # deliberately malformed
    ]
    if query_arguments:
        calls += [
            lambda: layered.successors(nid("a"), **query_arguments),  # type: ignore[arg-type]  # deliberately malformed
            lambda: layered.predecessors(nid("a"), **query_arguments),  # type: ignore[arg-type]  # deliberately malformed
        ]
    for call in calls:
        with pytest.raises(GraphError) as exc_info:
            call()
        assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


@pytest.mark.parametrize("starts", [[1], 7], ids=["non-string-start", "non-iterable"])
def test_malformed_starts_are_rejected(layered: DependencyGraph, starts: object) -> None:
    with pytest.raises(GraphError) as exc_info:
        layered.forward_reachable(starts)  # type: ignore[arg-type]  # deliberately malformed
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


def test_edge_lookup_raises_for_unknown_endpoints(layered: DependencyGraph) -> None:
    assert layered.edge(nid("a"), CALLS, nid("b")) is None
    for source, target in (("zz", "a"), ("a", "zz")):
        with pytest.raises(GraphError):
            layered.edge(nid(source), IMPORTS, nid(target))
