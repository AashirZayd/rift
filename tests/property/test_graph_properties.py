"""SPEC §10 Hypothesis properties of the dependency graph.

- reachability is transitive;
- forward/reverse symmetry: b ∈ forward(a) ⇔ a ∈ reverse(b);
- to_dict/from_dict round-trips;
- output is identical for any insertion order of nodes and (duplicate) edges;
- duplicates merge by the documented rule;
- filters are monotonic: a stronger min_confidence, fewer kinds or a smaller max_depth never
  reach more;
- every explanation path is a shortest path whose edges pass the filters, with the fewest weak
  edges and then the smallest node IDs, checked against brute-force enumeration of all shortest
  paths (also under max_depth).
"""

import json
from dataclasses import dataclass
from itertools import pairwise

from hypothesis import example, given
from hypothesis import strategies as st

from rift.core.canonical import canonical_json
from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, Edge, EdgeFlag, EdgeKind, EdgeOrigin
from rift.graph.merge import restriction_key
from rift.graph.multigraph import DependencyGraph
from rift.graph.traversal import Reachability
from tests.unit.graph.helpers import edge, graph_of, nid

MAX_NODES = 7


@dataclass(frozen=True)
class Spec:
    names: tuple[str, ...]
    edges: tuple[Edge, ...]

    def build(self) -> DependencyGraph:
        return graph_of(self.names, self.edges)


@st.composite
def specs(draw: st.DrawFn) -> Spec:
    size = draw(st.integers(min_value=1, max_value=MAX_NODES))
    names = tuple(f"m{i}" for i in range(size))
    index = st.integers(min_value=0, max_value=size - 1)
    raw = draw(
        st.lists(
            st.tuples(
                index,
                index,
                st.sampled_from(EdgeKind),
                st.sampled_from(EdgeOrigin),
                st.sampled_from(Confidence),
                st.frozensets(st.sampled_from(EdgeFlag)),
                st.frozensets(st.integers(min_value=0, max_value=3)),
            ),
            min_size=size,  # dense enough for diamonds, so path ties really occur
            max_size=4 * size,
        )
    )
    edges = tuple(
        edge(
            names[s],
            names[t],
            kind=kind,
            origin=origin,
            confidence=conf,
            flags=flags,
            evidence=evidence,
        )
        for s, t, kind, origin, conf, flags, evidence in raw
    )
    return Spec(names, edges)


kind_filters = st.none() | st.frozensets(st.sampled_from(EdgeKind))
confidences = st.sampled_from(Confidence)


def _qualifies(e: Edge, kinds: frozenset[EdgeKind] | None, floor: Confidence) -> bool:
    return (kinds is None or e.kind in kinds) and e.confidence >= floor


def _reach(
    graph: DependencyGraph,
    start: NodeId,
    *,
    reverse: bool = False,
    kinds: frozenset[EdgeKind] | None = None,
    floor: Confidence = Confidence.UNKNOWN,
) -> Reachability:
    method = graph.reverse_reachable if reverse else graph.forward_reachable
    return method(start, kinds=kinds, min_confidence=floor)


@given(specs(), kind_filters, confidences)
def test_reachability_is_transitive(
    spec: Spec, kinds: frozenset[EdgeKind] | None, floor: Confidence
) -> None:
    graph = spec.build()
    reach = {nid(a): set(_reach(graph, nid(a), kinds=kinds, floor=floor).nodes) for a in spec.names}
    for a, from_a in reach.items():
        for b in from_a:
            assert reach[b] <= from_a, (a, b)


@given(specs(), kind_filters, confidences)
def test_forward_and_reverse_are_symmetric(
    spec: Spec, kinds: frozenset[EdgeKind] | None, floor: Confidence
) -> None:
    graph = spec.build()
    forward = {
        nid(a): set(_reach(graph, nid(a), kinds=kinds, floor=floor).nodes) for a in spec.names
    }
    reverse = {
        nid(b): set(_reach(graph, nid(b), reverse=True, kinds=kinds, floor=floor).nodes)
        for b in spec.names
    }
    for a, from_a in forward.items():
        for b, to_b in reverse.items():
            assert (b in from_a) == (a in to_b)


@given(specs())
def test_serialization_round_trips(spec: Spec) -> None:
    graph = spec.build()
    text = canonical_json(graph.to_dict())
    again = DependencyGraph.from_dict(json.loads(text))
    assert again == graph
    assert canonical_json(again.to_dict()) == text


@given(specs(), st.data())
def test_output_is_identical_for_any_insertion_order(spec: Spec, data: st.DataObject) -> None:
    names = data.draw(st.permutations(spec.names))
    edges = data.draw(st.permutations(spec.edges))
    first, second = spec.build(), graph_of(names, edges)
    assert canonical_json(first.to_dict()) == canonical_json(second.to_dict())
    for name in spec.names:
        for reverse in (False, True):
            one, two = (_reach(g, nid(name), reverse=reverse) for g in (first, second))
            assert one.nodes == two.nodes
            for node in one.nodes:
                assert one.path(node) == two.path(node)
                assert one.path_edges(node) == two.path_edges(node)


@given(specs())
def test_duplicates_merge_by_the_documented_rule(spec: Spec) -> None:
    """Batch form of ADR-0009 §6: among all occurrences of one (source, kind, target), the
    least-restricted group wins; the stored edge has that group's strongest confidence and the
    union of its flags and evidence. Never stronger than an occurrence in the same context."""
    groups: dict[tuple[NodeId, EdgeKind, NodeId], list[Edge]] = {}
    for e in spec.edges:
        groups.setdefault((e.source, e.kind, e.target), []).append(e)
    graph = spec.build()
    assert graph.edge_count == len(groups)
    for (source, kind, target), occurrences in groups.items():
        least = min(restriction_key(e) for e in occurrences)
        winners = [e for e in occurrences if restriction_key(e) == least]
        stored = graph.edge(source, kind, target)
        assert stored is not None
        assert stored.confidence is max((e.confidence for e in winners), key=lambda c: c.rank)
        assert stored.flags == frozenset().union(*(e.flags for e in winners))
        assert stored.evidence_ids == tuple(sorted({i for e in winners for i in e.evidence_ids}))
        assert restriction_key(stored) == least


@given(specs(), kind_filters, st.data())
def test_min_confidence_filter_is_monotonic(
    spec: Spec, kinds: frozenset[EdgeKind] | None, data: st.DataObject
) -> None:
    graph = spec.build()
    low, high = sorted(
        data.draw(st.lists(confidences, min_size=2, max_size=2)), key=lambda c: c.rank
    )
    for name in spec.names:
        for reverse in (False, True):
            weak = set(_reach(graph, nid(name), reverse=reverse, kinds=kinds, floor=low).nodes)
            strong = set(_reach(graph, nid(name), reverse=reverse, kinds=kinds, floor=high).nodes)
            assert strong <= weak


@given(specs(), st.frozensets(st.sampled_from(EdgeKind)), st.integers(min_value=0, max_value=4))
def test_kind_and_depth_filters_are_monotonic(
    spec: Spec, kinds: frozenset[EdgeKind], depth: int
) -> None:
    graph = spec.build()
    for name in spec.names:
        everything = set(_reach(graph, nid(name)).nodes)
        assert set(_reach(graph, nid(name), kinds=kinds).nodes) <= everything
        shallow = set(graph.forward_reachable(nid(name), max_depth=depth).nodes)
        deeper = set(graph.forward_reachable(nid(name), max_depth=depth + 1).nodes)
        assert shallow <= deeper <= everything


def _all_shortest_paths(
    nodes: list[NodeId],
    starts: frozenset[NodeId],
    hops: dict[NodeId, set[NodeId]],
) -> dict[NodeId, list[tuple[NodeId, ...]]]:
    """Brute force, independent of the BFS tie-breaking: distances by repeated relaxation, then
    every path that advances exactly one distance level per hop."""
    dist = dict.fromkeys(starts, 0)
    changed = True
    while changed:
        changed = False
        for u in list(dist):
            for v in hops[u]:
                if v not in dist or dist[v] > dist[u] + 1:
                    dist[v] = dist[u] + 1
                    changed = True
    layer: list[tuple[NodeId, ...]] = [(s,) for s in sorted(starts)]
    paths: dict[NodeId, list[tuple[NodeId, ...]]] = {n: [] for n in nodes}
    level = 0
    while layer:
        for path in layer:
            if dist[path[-1]] == level:
                paths[path[-1]].append(path)
        level += 1
        layer = [(*p, v) for p in layer for v in hops[p[-1]] if dist.get(v) == level]
    return {n: p for n, p in paths.items() if p}


DIAMOND = Spec(
    ("m0", "m1", "m2", "m3"),
    (edge("m0", "m2"), edge("m0", "m1"), edge("m2", "m3"), edge("m1", "m3")),
)
WEAK_DIAMOND = Spec(  # the lexicographically smaller route has the weaker link
    ("m0", "m1", "m2", "m3"),
    (
        edge("m0", "m1", confidence=Confidence.POSSIBLE),
        edge("m1", "m3"),
        edge("m0", "m2"),
        edge("m2", "m3", confidence=Confidence.LIKELY),
    ),
)
# Paths diverge at depth 1 and tie again at depth 3; the level-2 node IDs sort the other way
# (m3 < m4), so only a correct per-level lexicographic rank picks m0, m1, m4, m5.
DEEP_TIE = Spec(
    ("m0", "m1", "m2", "m3", "m4", "m5"),
    tuple(
        edge(a, b)
        for a, b in (
            ("m0", "m1"),
            ("m0", "m2"),
            ("m1", "m4"),
            ("m2", "m3"),
            ("m4", "m5"),
            ("m3", "m5"),
        )
    ),
)
_COST_INDEX = {Confidence.UNKNOWN: 0, Confidence.POSSIBLE: 1, Confidence.LIKELY: 2}


@dataclass(frozen=True)
class Filters:
    kinds: frozenset[EdgeKind] | None
    floor: Confidence
    max_depth: int | None

    def admits(self, e: Edge) -> bool:
        return _qualifies(e, self.kinds, self.floor)


def _check_paths(
    graph: DependencyGraph, starts: frozenset[NodeId], *, reverse: bool, filters: Filters
) -> None:
    method = graph.reverse_reachable if reverse else graph.forward_reachable
    result = method(
        starts, kinds=filters.kinds, min_confidence=filters.floor, max_depth=filters.max_depth
    )
    nodes = [n.id for n in graph.nodes()]
    hops: dict[NodeId, set[NodeId]] = {n: set() for n in nodes}
    best: dict[tuple[NodeId, NodeId], Edge] = {}  # hop (in traversal order) -> explaining edge
    for e in graph.edges():
        if not filters.admits(e):
            continue
        hop = (e.target, e.source) if reverse else (e.source, e.target)
        hops[hop[0]].add(hop[1])
        current = best.get(hop)
        # The strongest qualifying edge explains a hop; a tie goes to the smaller kind.
        if current is None or (-e.confidence.rank, e.kind.value) < (
            -current.confidence.rank,
            current.kind.value,
        ):
            best[hop] = e

    def cost(path: tuple[NodeId, ...]) -> tuple[int, int, int]:
        counts = [0, 0, 0]
        for hop in pairwise(path):
            index = _COST_INDEX.get(best[hop].confidence)
            if index is not None:
                counts[index] += 1
        return (counts[0], counts[1], counts[2])

    expected = {
        node: candidates
        for node, candidates in _all_shortest_paths(nodes, starts, hops).items()
        if filters.max_depth is None or len(candidates[0]) - 1 <= filters.max_depth
    }
    assert result.nodes == tuple(sorted(expected))
    for node, candidates in expected.items():
        path = result.path(node)
        # Fewest weak edges first, then the lexicographically smallest path.
        assert path == min(candidates, key=lambda p: (cost(p), p))
        assert result.depth(node) == len(path) - 1
        assert result.path_edges(node) == tuple(best[hop] for hop in pairwise(path))


@given(
    specs(),
    kind_filters,
    confidences,
    st.none() | st.integers(min_value=0, max_value=MAX_NODES),
    st.sets(st.integers(min_value=0, max_value=MAX_NODES - 1), min_size=2),
)
@example(DIAMOND, None, Confidence.UNKNOWN, None, {0, 3})  # m3 via m1, m0 via m1 (smaller IDs)
@example(WEAK_DIAMOND, None, Confidence.UNKNOWN, None, {0, 3})  # m3 via m2 (fewer weak links)
@example(DEEP_TIE, None, Confidence.UNKNOWN, None, {0, 5})  # m5 via m1, m4 (lexicographic rank)
def test_paths_are_the_strongest_then_smallest_shortest_paths(
    spec: Spec,
    kinds: frozenset[EdgeKind] | None,
    floor: Confidence,
    max_depth: int | None,
    start_indexes: set[int],
) -> None:
    """Every node as a single start plus one multi-start set, in both directions, with the drawn
    filters and unfiltered."""
    graph = spec.build()
    several = frozenset(nid(spec.names[i % len(spec.names)]) for i in start_indexes)
    drawn = Filters(kinds, floor, max_depth)
    for starts in [*(frozenset({nid(name)}) for name in spec.names), several]:
        for reverse in (False, True):
            for filters in {drawn, Filters(None, Confidence.UNKNOWN, None)}:
                _check_paths(graph, starts, reverse=reverse, filters=filters)
