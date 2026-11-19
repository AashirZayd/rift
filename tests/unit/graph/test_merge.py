"""ADR-0009 §6: an edge's key is (source, kind, target, restricting flags). Edges with the same
key merge by a fixed, order-free rule; edges that differ only in restricting flags are kept in
parallel."""

import itertools

import pytest

from rift.core.errors import ErrorCode
from rift.core.models.graph import Confidence, EdgeFlag, EdgeKind, EdgeOrigin
from rift.graph.errors import GraphError
from rift.graph.merge import edge_key, merge_edges, restriction_key
from tests.unit.graph.helpers import edge, ev, graph_of, nid

TC = EdgeFlag.TYPE_CHECKING_ONLY
COND = EdgeFlag.CONDITIONAL
STAR = EdgeFlag.STAR_IMPORT
DYN = EdgeFlag.DYNAMIC


def test_strongest_confidence_wins_and_brings_its_origin() -> None:
    weak = edge("a", "b", confidence=Confidence.POSSIBLE, origin=EdgeOrigin.HEURISTIC)
    strong = edge("a", "b", confidence=Confidence.LIKELY, origin=EdgeOrigin.NAME_RESOLUTION)
    for first, second in ((weak, strong), (strong, weak)):
        merged = merge_edges(first, second)
        assert (merged.confidence, merged.origin) == (Confidence.LIKELY, EdgeOrigin.NAME_RESOLUTION)


def test_equal_confidence_takes_the_first_declared_origin() -> None:
    a = edge("a", "b", origin=EdgeOrigin.PLUGIN)
    b = edge("a", "b", origin=EdgeOrigin.AST_STATIC)
    assert merge_edges(a, b).origin is EdgeOrigin.AST_STATIC
    assert merge_edges(b, a).origin is EdgeOrigin.AST_STATIC


def test_evidence_ids_and_provenance_flags_are_unioned() -> None:
    merged = merge_edges(
        edge("a", "b", evidence=(3, 1), flags={TC, STAR}),
        edge("a", "b", evidence=(2, 3), flags={TC, DYN}),
    )
    assert merged.evidence_ids == (ev(1), ev(2), ev(3))
    assert merged.flags == {TC, STAR, DYN}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param({TC}, set(), id="type-checking-vs-runtime"),
        pytest.param({COND}, set(), id="conditional-vs-unconditional"),
        pytest.param({TC}, {COND}, id="different-restrictions"),
        pytest.param({TC, COND}, {TC}, id="superset-of-restrictions"),
        pytest.param({TC, STAR}, {DYN}, id="provenance-does-not-matter-restrictions-do"),
    ],
)
def test_different_restricting_flags_are_different_keys(
    first: set[EdgeFlag], second: set[EdgeFlag]
) -> None:
    a, b = edge("a", "b", flags=first), edge("a", "b", flags=second)
    assert edge_key(a) != edge_key(b)
    with pytest.raises(GraphError) as exc_info:
        merge_edges(a, b)
    assert exc_info.value.code is ErrorCode.GRAPH_CONFLICT


def test_provenance_flags_are_not_part_of_the_key() -> None:
    assert edge_key(edge("a", "b", flags={STAR})) == edge_key(edge("a", "b", flags={DYN}))


def test_type_checking_and_runtime_edges_are_both_kept() -> None:
    """Review decision 1: a certain TYPE_CHECKING import and a likely runtime import of the
    same module are two edges."""
    typing_only = edge("a", "b", confidence=Confidence.CERTAIN, flags={TC}, evidence=(1,))
    runtime = edge("a", "b", confidence=Confidence.LIKELY, flags={DYN}, evidence=(2,))
    graph = graph_of(["a", "b"], [typing_only, runtime])
    assert graph.edge_count == 2
    assert graph.edge(nid("a"), EdgeKind.IMPORTS, nid("b")) == runtime
    assert graph.edge(nid("a"), EdgeKind.IMPORTS, nid("b"), {TC}) == typing_only
    assert graph.edges_between(nid("a"), EdgeKind.IMPORTS, nid("b")) == (runtime, typing_only)
    assert graph.successors(nid("a")) == (runtime, typing_only)
    assert graph.predecessors(nid("b")) == (runtime, typing_only)


def test_edge_lookup_rejects_non_restricting_flags() -> None:
    graph = graph_of(["a", "b"], [edge("a", "b")])
    with pytest.raises(GraphError) as exc_info:
        graph.edge(nid("a"), EdgeKind.IMPORTS, nid("b"), {STAR})
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


def test_restriction_order_is_least_restricted_first() -> None:
    order = sorted(
        [frozenset({TC, COND}), frozenset({TC}), frozenset(), frozenset({COND})],
        key=restriction_key,
    )
    assert order == [frozenset(), frozenset({COND}), frozenset({TC}), frozenset({TC, COND})]


def test_merge_is_order_independent_over_many_occurrences() -> None:
    occurrences = [
        edge("a", "b", confidence=Confidence.POSSIBLE, flags={TC, STAR}, evidence=(1,)),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.PLUGIN, flags={DYN}),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.AST_STATIC, evidence=(2,)),
        edge("a", "b", confidence=Confidence.LIKELY, flags={TC}, evidence=(3,)),
    ]
    results = set()
    for order in itertools.permutations(occurrences):
        graph = graph_of(["a", "b"], order)
        results.add(graph.edges())
    (only,) = results
    runtime, typing_only = only
    assert (runtime.confidence, runtime.origin, runtime.flags, runtime.evidence_ids) == (
        Confidence.CERTAIN,
        EdgeOrigin.AST_STATIC,
        frozenset({DYN}),
        (ev(2),),
    )
    assert (typing_only.confidence, typing_only.flags, typing_only.evidence_ids) == (
        Confidence.LIKELY,
        frozenset({TC, STAR}),
        (ev(1), ev(3)),
    )


def test_edges_of_different_kind_are_not_merged() -> None:
    with pytest.raises(GraphError) as exc_info:
        merge_edges(edge("a", "b"), edge("a", "b", kind=EdgeKind.CALLS))
    assert exc_info.value.code is ErrorCode.GRAPH_CONFLICT


def test_graph_stores_one_merged_edge_per_key() -> None:
    graph = graph_of(
        ["a", "b"],
        [
            edge("a", "b", confidence=Confidence.POSSIBLE),
            edge("a", "b", confidence=Confidence.CERTAIN),
            edge("a", "b", kind=EdgeKind.REFERENCES),
        ],
    )
    assert graph.edge_count == 2
    stored = graph.edge(nid("a"), EdgeKind.IMPORTS, nid("b"))
    assert stored is not None
    assert stored.confidence is Confidence.CERTAIN
    assert graph.predecessors(nid("b"), kinds={EdgeKind.IMPORTS}) == (stored,)


def test_traversal_explains_with_the_strongest_parallel_edge() -> None:
    weak_runtime = edge("a", "b", confidence=Confidence.POSSIBLE)
    strong_typing = edge("a", "b", confidence=Confidence.CERTAIN, flags={TC})
    graph = graph_of(["a", "b"], [weak_runtime, strong_typing])
    assert graph.forward_reachable(nid("a")).path_edges(nid("b")) == (strong_typing,)
    # Restricted to likely or stronger, only the TYPE_CHECKING edge qualifies.
    likely = graph.forward_reachable(nid("a"), min_confidence=Confidence.LIKELY)
    assert likely.path_edges(nid("b")) == (strong_typing,)
    # On a confidence tie, the less-restricted parallel edge explains the hop.
    tied = graph_of(["a", "b"], [edge("a", "b", flags={TC}), edge("a", "b", flags={COND})])
    (step,) = tied.forward_reachable(nid("a")).path_edges(nid("b"))
    assert step.flags == {COND}
