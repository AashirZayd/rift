"""ADR-0009: duplicate edges (same source, kind, target) merge by a fixed, order-free rule."""

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
        pytest.param({TC}, set(), set(), id="runtime-import-beats-type-checking-only"),
        pytest.param({TC}, {TC}, {TC}, id="type-checking-only-everywhere-stays"),
        pytest.param({COND}, set(), set(), id="unconditional-beats-conditional"),
        pytest.param({TC}, {COND}, set(), id="different-restrictions-cancel"),
        pytest.param({STAR}, set(), {STAR}, id="provenance-is-unioned"),
        pytest.param({STAR, TC}, {DYN, TC}, {STAR, DYN, TC}, id="both-rules-together"),
    ],
)
def test_flags_merge_by_meaning(
    first: set[EdgeFlag], second: set[EdgeFlag], expected: set[EdgeFlag]
) -> None:
    a, b = edge("a", "b", flags=first), edge("a", "b", flags=second)
    assert merge_edges(a, b).flags == expected
    assert merge_edges(b, a).flags == expected


def test_merge_is_order_independent_over_many_occurrences() -> None:
    occurrences = [
        edge("a", "b", confidence=Confidence.POSSIBLE, flags={TC, STAR}, evidence=(1,)),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.PLUGIN, flags={TC}),
        edge("a", "b", confidence=Confidence.CERTAIN, origin=EdgeOrigin.AST_STATIC, flags={DYN}),
    ]
    results = set()
    for order in itertools.permutations(occurrences):
        merged = order[0]
        for item in order[1:]:
            merged = merge_edges(merged, item)
        results.add(merged)
    (only,) = results
    assert (only.confidence, only.origin) == (Confidence.CERTAIN, EdgeOrigin.AST_STATIC)
    assert only.flags == {STAR, DYN}
    assert only.evidence_ids == (ev(1),)


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
