"""Iterative breadth-first reachability with shortest explanation paths (SPEC §10).

No recursion anywhere: a 100k-node chain or a cycle-heavy graph never touches the interpreter
recursion limit, and the visited map makes every node enter the queue at most once.

Determinism (ADR-0009): starts are visited in sorted order, and each node's neighbours are
expanded in sorted order. The first node to discover a neighbour is therefore its parent, so every
reached node gets the **lexicographically smallest shortest path** (compared as a sequence of node
IDs from the start). When several edge kinds join the same two nodes, the path uses the strongest
qualifying edge, and on a tie the kind with the smaller value.
"""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, Edge, EdgeKind
from rift.graph.errors import GraphError

# node -> kind -> neighbour -> edge (forward: neighbour is the target; reverse: the source).
Adjacency = Mapping[NodeId, Mapping[EdgeKind, Mapping[NodeId, Edge]]]

_ALL_KINDS = tuple(sorted(EdgeKind, key=lambda kind: kind.value))
_NO_NEIGHBOURS: Mapping[NodeId, Edge] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Step:
    """How a reached node was first discovered: from ``previous`` across ``edge``."""

    previous: NodeId
    edge: Edge


@dataclass(frozen=True, slots=True, kw_only=True)
class Reachability:
    """The nodes reached from ``starts`` (starts included, at depth 0), sorted by ID.

    ``path(node)`` is the shortest path from a start to ``node``. ``path_edges(node)[i]`` joins
    ``path[i]`` and ``path[i + 1]``: its source is ``path[i]`` when the traversal was forward,
    and its target is ``path[i]`` when it was reverse. Paths are rebuilt on demand from parent
    links, so memory stays linear in the number of reached nodes.
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
        """Shortest path from a start to ``node``, both included."""
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


def normalize_starts(starts: NodeId | Iterable[NodeId]) -> tuple[NodeId, ...]:
    """One start or many; sorted and without duplicates."""
    if isinstance(starts, str):
        return (starts,)
    return tuple(sorted(set(starts)))


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
    if max_depth is not None and (isinstance(max_depth, bool) or max_depth < 0):
        raise GraphError("max_depth must be a non-negative int", code=ErrorCode.INVALID_ARGUMENT)
    if not isinstance(min_confidence, Confidence):
        raise GraphError("min_confidence must be a Confidence", code=ErrorCode.INVALID_ARGUMENT)
    wanted = _ALL_KINDS if kinds is None else tuple(sorted(set(kinds), key=lambda k: k.value))
    ordered = normalize_starts(starts)
    for start in ordered:
        if start not in adjacency:
            raise GraphError(f"unknown start node {start!r}", code=ErrorCode.UNKNOWN_NODE)

    steps: dict[NodeId, Step | None] = dict.fromkeys(ordered)
    depths: dict[NodeId, int] = dict.fromkeys(ordered, 0)
    queue = deque(ordered)
    while queue:
        node = queue.popleft()
        depth = depths[node]
        if max_depth is not None and depth >= max_depth:
            continue
        by_kind = adjacency[node]
        best: dict[NodeId, Edge] = {}
        for kind in wanted:
            for neighbour, edge in by_kind.get(kind, _NO_NEIGHBOURS).items():
                if neighbour in steps or edge.confidence < min_confidence:
                    continue
                current = best.get(neighbour)
                if current is None or edge.confidence > current.confidence:
                    best[neighbour] = edge
        for neighbour in sorted(best):
            steps[neighbour] = Step(node, best[neighbour])
            depths[neighbour] = depth + 1
            queue.append(neighbour)
    return Reachability(
        starts=ordered,
        nodes=tuple(sorted(steps)),
        steps=MappingProxyType(steps),
        depths=MappingProxyType(depths),
    )
