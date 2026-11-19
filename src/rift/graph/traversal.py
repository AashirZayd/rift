"""Iterative breadth-first reachability with shortest explanation paths (SPEC §10, ADR-0009).

No recursion anywhere: a 100k-node chain or a cycle-heavy graph never touches the interpreter
recursion limit, and the visited map lets every node be expanded at most once.

Which shortest path explains a node? Among all shortest qualifying paths from any start:

1. the path with the **fewest weak edges** wins. Paths are compared by
   ``(number of unknown edges, number of possible edges, number of likely edges)``, so a path
   whose weakest link is stronger always wins, and explanations prefer confirmed evidence;
2. a remaining tie goes to the **lexicographically smallest** path, compared as a sequence of
   node IDs from the start.

Each hop uses the strongest qualifying edge between its two nodes, parallel edges included
(ADR-0009 §6). Ties go to the smaller kind value, then to the less-restricted edge. The cost is
additive, so the BFS computes this exactly, level by level, from each node's own best path.
Hypothesis checks it against brute force.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, Edge, EdgeFlag, EdgeKind
from rift.graph.errors import GraphError
from rift.graph.merge import restriction_key

# node -> kind -> neighbour -> restricting flags -> edge (forward: neighbour is the target;
# reverse: the source). Parallel edges differ only in their restricting flags (ADR-0009 §6).
Adjacency = Mapping[NodeId, Mapping[EdgeKind, Mapping[NodeId, Mapping[frozenset[EdgeFlag], Edge]]]]
# (unknown, possible, likely) edge counts along a path; smaller is stronger.
Cost = tuple[int, int, int]

_ALL_KINDS = tuple(sorted(EdgeKind, key=lambda kind: kind.value))
_NO_NEIGHBOURS: Mapping[NodeId, Mapping[frozenset[EdgeFlag], Edge]] = MappingProxyType({})
_ZERO: Cost = (0, 0, 0)
_EDGE_COST: Mapping[Confidence, Cost] = MappingProxyType(
    {
        Confidence.UNKNOWN: (1, 0, 0),
        Confidence.POSSIBLE: (0, 1, 0),
        Confidence.LIKELY: (0, 0, 1),
        Confidence.CERTAIN: _ZERO,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    """How a reached node was discovered: from ``previous`` across ``edge``."""

    previous: NodeId
    edge: Edge


@dataclass(frozen=True, slots=True, kw_only=True)
class Reachability:
    """The nodes reached from ``starts`` (starts included, at depth 0), sorted by ID.

    ``path(node)`` is the explaining shortest path from a start to ``node`` (module docstring).
    ``path_edges(node)[i]`` joins ``path[i]`` and ``path[i + 1]``: its source is ``path[i]`` in a
    forward traversal and its target is ``path[i]`` in a reverse one. Paths are rebuilt on demand
    from parent links, so memory stays linear in the number of reached nodes.
    """

    starts: tuple[NodeId, ...]
    nodes: tuple[NodeId, ...]
    steps: Mapping[NodeId, Step | None]
    depths: Mapping[NodeId, int]

    def __contains__(self, node: object) -> bool:
        return node in self.steps

    def depth(self, node: NodeId) -> int:
        """Number of edges on the shortest path to ``node``."""
        return self.depths[self._known(node)]

    def path(self, node: NodeId) -> tuple[NodeId, ...]:
        """The explaining path from a start to ``node``, both included."""
        reversed_path = [self._known(node)]
        step = self.steps[node]
        while step is not None:
            reversed_path.append(step.previous)
            step = self.steps[step.previous]
        return tuple(reversed(reversed_path))

    def path_edges(self, node: NodeId) -> tuple[Edge, ...]:
        """The edges along :meth:`path`, in path order."""
        reversed_edges: list[Edge] = []
        step = self.steps[self._known(node)]
        while step is not None:
            reversed_edges.append(step.edge)
            step = self.steps[step.previous]
        return tuple(reversed(reversed_edges))

    def _known(self, node: NodeId) -> NodeId:
        if node not in self.steps:
            raise GraphError(f"node {node!r} was not reached", code=ErrorCode.UNKNOWN_NODE)
        return node


def _invalid(message: str) -> GraphError:
    return GraphError(message, code=ErrorCode.INVALID_ARGUMENT)


def check_kinds(kinds: Iterable[EdgeKind] | None) -> tuple[EdgeKind, ...]:
    """Validated kind filter, sorted by value (``None``: every kind). A bare string or enum is
    rejected: it would iterate as characters."""
    if kinds is None:
        return _ALL_KINDS
    if isinstance(kinds, str) or not isinstance(kinds, Iterable):
        raise _invalid("kinds must be a collection of EdgeKind or None")
    wanted = set(kinds)
    if not all(isinstance(kind, EdgeKind) for kind in wanted):
        raise _invalid("kinds must contain only EdgeKind values")
    return tuple(sorted(wanted, key=lambda kind: kind.value))


def check_confidence(min_confidence: Confidence) -> Confidence:
    if not isinstance(min_confidence, Confidence):
        raise _invalid("min_confidence must be a Confidence")
    return min_confidence


def check_max_depth(max_depth: int | None) -> int | None:
    if max_depth is not None and (
        type(max_depth) is not int or max_depth < 0  # bool and float are rejected
    ):
        raise _invalid("max_depth must be a non-negative int or None")
    return max_depth


def normalize_starts(starts: NodeId | Iterable[NodeId]) -> tuple[NodeId, ...]:
    """One start or many; sorted and without duplicates."""
    if isinstance(starts, str):
        return (starts,)
    if not isinstance(starts, Iterable):
        raise _invalid("starts must be a node ID or a collection of node IDs")
    unique = set(starts)
    if not all(isinstance(start, str) for start in unique):
        raise _invalid("starts must be node IDs")
    return tuple(sorted(unique))


def _plus(cost: Cost, edge: Edge) -> Cost:
    extra = _EDGE_COST[edge.confidence]
    return (cost[0] + extra[0], cost[1] + extra[1], cost[2] + extra[2])


def breadth_first(
    adjacency: Adjacency,
    starts: NodeId | Iterable[NodeId],
    *,
    kinds: Iterable[EdgeKind] | None,
    min_confidence: Confidence,
    max_depth: int | None,
) -> Reachability:
    """Nodes reachable from ``starts`` across edges of ``kinds`` (all kinds when ``None``) whose
    confidence is at least ``min_confidence``, at most ``max_depth`` edges away."""
    wanted = check_kinds(kinds)
    floor = check_confidence(min_confidence)
    limit = check_max_depth(max_depth)
    ordered = normalize_starts(starts)
    level = list(ordered)
    for start in level:
        if start not in adjacency:
            raise GraphError(f"unknown start node {start!r}", code=ErrorCode.UNKNOWN_NODE)

    steps: dict[NodeId, Step | None] = dict.fromkeys(level)
    depths: dict[NodeId, int] = dict.fromkeys(level, 0)
    # Per level: each node's best-path cost, and the lexicographic rank of its path.
    cost: dict[NodeId, Cost] = dict.fromkeys(level, _ZERO)
    rank = {node: index for index, node in enumerate(level)}
    depth = 0
    while level and (limit is None or depth < limit):
        offers: dict[NodeId, tuple[Cost, int, NodeId, Edge]] = {}
        for node in level:
            for neighbour, edge in _best_edges(adjacency[node], wanted, floor, steps).items():
                offer = (_plus(cost[node], edge), rank[node], node, edge)
                current = offers.get(neighbour)
                if current is None or offer[:2] < current[:2]:
                    offers[neighbour] = offer
        depth += 1
        # The next level in lexicographic path order: by the parent's rank, then by node ID.
        level = sorted(offers, key=lambda n: (offers[n][1], n))
        cost, rank = {}, {}
        for index, node in enumerate(level):
            path_cost, _, previous, edge = offers[node]
            steps[node] = Step(previous=previous, edge=edge)
            depths[node] = depth
            cost[node] = path_cost
            rank[node] = index
    return Reachability(
        starts=ordered,
        nodes=tuple(sorted(steps)),
        steps=MappingProxyType(steps),
        depths=MappingProxyType(depths),
    )


def _best_edges(
    by_kind: Mapping[EdgeKind, Mapping[NodeId, Mapping[frozenset[EdgeFlag], Edge]]],
    wanted: tuple[EdgeKind, ...],
    floor: Confidence,
    visited: Mapping[NodeId, object],
) -> dict[NodeId, Edge]:
    """Per unvisited neighbour, the strongest qualifying edge. Ties go to the smaller kind, then
    to the less-restricted parallel edge: kinds and restriction sets are visited in that order,
    and only a strictly stronger edge replaces the current one."""
    best: dict[NodeId, Edge] = {}
    for kind in wanted:  # sorted by value
        for neighbour, parallel in by_kind.get(kind, _NO_NEIGHBOURS).items():
            if neighbour in visited:
                continue
            for flags in sorted(parallel, key=restriction_key):
                edge = parallel[flags]
                if edge.confidence < floor:
                    continue
                current = best.get(neighbour)
                if current is None or edge.confidence > current.confidence:
                    best[neighbour] = edge
    return best
