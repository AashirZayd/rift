"""ADR-0009: duplicate edges (same source, kind, target) merge by a fixed, order-free rule:
the least-restricted group of occurrences wins, then the strongest occurrence within it."""

import itertools

import pytest

from rift.core.errors import ErrorCode
from rift.core.models.graph import Confidence, EdgeFlag, EdgeKind, EdgeOrigin
from rift.graph.errors import GraphError
from rift.graph.merge import merge_edges
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


def test_evidence_ids_are_unioned_and_sorted() -> None:
    merged = merge_edges(edge("a", "b", evidence=(3, 1)), edge("a", "b", evidence=(2, 3)))
    assert merged.evidence_ids == (ev(1), ev(2), ev(3))


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param({TC}, set(), set(), id="runtime-import-subsumes-type-checking-only"),
        pytest.param({TC}, {TC}, {TC}, id="type-checking-only-everywhere-stays"),
        pytest.param({COND}, set(), set(), id="unconditional-subsumes-conditional"),
        pytest.param({TC}, {COND}, {COND}, id="conditional-runtime-beats-type-checking"),
        pytest.param({TC, COND}, {TC}, {TC}, id="fewer-restrictions-win"),
        pytest.param({STAR}, set(), {STAR}, id="provenance-unioned-within-a-group"),
        pytest.param({STAR, TC}, {DYN, TC}, {STAR, DYN, TC}, id="same-group-both-rules"),
        pytest.param({STAR, TC}, {DYN}, {DYN}, id="subsumed-provenance-is-not-carried"),
    ],
)
def test_flags_merge_by_restriction_group(
    first: set[EdgeFlag], second: set[EdgeFlag], expected: set[EdgeFlag]
) -> None:
    a, b = edge("a", "b", flags=first), edge("a", "b", flags=second)
    assert merge_edges(a, b).flags == expected
    assert merge_edges(b, a).flags == expected


def test_confidence_is_never_inflated_by_a_subsumed_occurrence() -> None:
    """Review finding: a certain TYPE_CHECKING import plus a likely dynamic runtime import is a
    likely runtime dependency, never a certain one."""
    typing_only = edge("a", "b", confidence=Confidence.CERTAIN, flags={TC}, evidence=(1,))
    runtime = edge("a", "b", confidence=Confidence.LIKELY, flags={DYN}, evidence=(2,))
    for pair in ((typing_only, runtime), (runtime, typing_only)):
        merged = merge_edges(*pair)
        assert (merged.confidence, merged.flags, merged.evidence_ids) == (
            Confidence.LIKELY,
            frozenset({DYN}),
            (ev(2),),
        )


def test_merge_is_order_independent_over_many_occurrences() -> None:
    occurrences = [
        edge("a", "b", confidence=Confidence.POSSIBLE, flags={TC, STAR}, evidence=(1,)),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.PLUGIN, flags={DYN}),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.AST_STATIC, evidence=(2,)),
        edge("a", "b", confidence=Confidence.LIKELY, flags={TC}, evidence=(3,)),
    ]
    results = set()
    for order in itertools.permutations(occurrences):
        merged = order[0]
        for item in order[1:]:
            merged = merge_edges(merged, item)
        results.add(merged)
    (only,) = results
    assert (only.confidence, only.origin) == (Confidence.CERTAIN, EdgeOrigin.AST_STATIC)
    assert only.flags == {DYN}
    assert only.evidence_ids == (ev(2),)


def test_edges_of_different_identity_are_not_merged() -> None:
    with pytest.raises(GraphError) as exc_info:
        merge_edges(edge("a", "b"), edge("a", "b", kind=EdgeKind.CALLS))
    assert exc_info.value.code is ErrorCode.GRAPH_CONFLICT


def test_graph_stores_one_merged_edge_per_identity() -> None:
    graph = graph_of(
        ["a", "b"],
        [
            edge("a", "b", confidence=Confidence.POSSIBLE, flags={TC}),
            edge("a", "b", confidence=Confidence.CERTAIN),
            edge("a", "b", kind=EdgeKind.REFERENCES),
        ],
    )
    assert graph.edge_count == 2
    stored = graph.edge(nid("a"), EdgeKind.IMPORTS, nid("b"))
    assert stored is not None
    assert (stored.confidence, stored.flags) == (Confidence.CERTAIN, frozenset())
    # The reverse index holds the same merged edge.
    assert graph.predecessors(nid("b"), kinds={EdgeKind.IMPORTS}) == (stored,)
