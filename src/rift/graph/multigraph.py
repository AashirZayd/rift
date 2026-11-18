"""The in-memory directed dependency multigraph (SPEC §10).

Adjacency is stored in both directions as ``node -> kind -> neighbour -> edge``. That makes it a
multigraph over edge kinds, with at most one edge per ``(source, kind, target)``: a duplicate is
merged by :func:`rift.graph.merge.merge_edges`. Dicts keep insertion order, but no output depends
on it. Every collection returned is sorted.
"""

from collections.abc import Iterable
from typing import Final

from rift.core.canonical import JsonValue
from rift.core.errors import ErrorCode, SerializationError
from rift.core.ids import NodeId
from rift.core.models.analysis import edge_sort_key
from rift.core.models.graph import Confidence, Edge, EdgeKind, Node
from rift.core.serialization import from_dict, to_dict
from rift.graph.errors import GraphError
from rift.graph.merge import merge_edges
from rift.graph.traversal import Reachability, breadth_first

GRAPH_FORMAT: Final = "rift.graph"
GRAPH_FORMAT_VERSION: Final = 1
_DOCUMENT_KEYS = frozenset({"format", "version", "nodes", "edges"})

_Adjacency = dict[NodeId, dict[EdgeKind, dict[NodeId, Edge]]]


class DependencyGraph:
    """A directed multigraph of :class:`Node` and :class:`Edge` values.

    ``add_edge`` rejects an edge whose source or target is not a node: the caller records it as
    unresolved instead (SPEC §10). Equality compares the node and edge sets.
    """

    __slots__ = ("_edge_count", "_in", "_nodes", "_out")
    __hash__ = None  # type: ignore[assignment]  # mutable container: unhashable (typeshed idiom)

    def __init__(self) -> None:
        self._nodes: dict[NodeId, Node] = {}
        self._out: _Adjacency = {}
        self._in: _Adjacency = {}
        self._edge_count = 0

    # ------------------------------------------------------------------ construction

    def add_node(self, node: Node) -> None:
        """Add ``node``. Re-adding an equal node is a no-op; a different node under the same ID
        is a conflict."""
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
            self._out[node.id] = {}
            self._in[node.id] = {}
        elif existing != node:
            raise GraphError(
                f"node {node.id!r} already exists with different contents",
                code=ErrorCode.GRAPH_CONFLICT,
            )

    def add_edge(self, edge: Edge) -> Edge:
        """Add ``edge``, merging it with an existing ``(source, kind, target)`` edge; returns the
        stored edge. Raises :class:`GraphError` if either endpoint is not a node."""
        for endpoint in (edge.source, edge.target):
            if endpoint not in self._nodes:
                raise GraphError(f"edge endpoint {endpoint!r} is not a node in the graph")
        by_target = self._out[edge.source].setdefault(edge.kind, {})
        existing = by_target.get(edge.target)
        stored = edge if existing is None else merge_edges(existing, edge)
        if existing is None:
            self._edge_count += 1
        by_target[edge.target] = stored
        self._in[edge.target].setdefault(edge.kind, {})[edge.source] = stored
        return stored

    # ------------------------------------------------------------------ queries

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return self._edge_count

    def has_node(self, node_id: NodeId) -> bool:
        return node_id in self._nodes

    def node(self, node_id: NodeId) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise GraphError(f"unknown node {node_id!r}") from None

    def nodes(self) -> tuple[Node, ...]:
        """Every node, sorted by ID."""
        return tuple(self._nodes[node_id] for node_id in sorted(self._nodes))

    def edges(self) -> tuple[Edge, ...]:
        """Every edge, sorted by ``(source, kind, target, ...)``."""
        return tuple(
            sorted(
                (
                    edge
                    for by_kind in self._out.values()
                    for by_target in by_kind.values()
                    for edge in by_target.values()
                ),
                key=edge_sort_key,
            )
        )

    def edge(self, source: NodeId, kind: EdgeKind, target: NodeId) -> Edge | None:
        """The edge ``source -kind-> target``, if present."""
        return self._out.get(source, {}).get(kind, {}).get(target)

    def successors(
        self,
        node_id: NodeId,
        *,
        kinds: Iterable[EdgeKind] | None = None,
        min_confidence: Confidence = Confidence.UNKNOWN,
    ) -> tuple[Edge, ...]:
        """Outgoing edges of ``node_id`` that pass the filters, sorted by ``(target, kind)``."""
        edges = self._incident(self._out, node_id, kinds, min_confidence)
        return tuple(sorted(edges, key=lambda e: (e.target, e.kind.value)))

    def predecessors(
        self,
        node_id: NodeId,
        *,
        kinds: Iterable[EdgeKind] | None = None,
        min_confidence: Confidence = Confidence.UNKNOWN,
    ) -> tuple[Edge, ...]:
        """Incoming edges of ``node_id`` that pass the filters, sorted by ``(source, kind)``."""
        edges = self._incident(self._in, node_id, kinds, min_confidence)
        return tuple(sorted(edges, key=lambda e: (e.source, e.kind.value)))

    def _incident(
        self,
        adjacency: _Adjacency,
        node_id: NodeId,
        kinds: Iterable[EdgeKind] | None,
        min_confidence: Confidence,
    ) -> list[Edge]:
        self.node(node_id)  # raises for an unknown node
        wanted = None if kinds is None else frozenset(kinds)
        return [
            edge
            for kind, by_neighbour in adjacency[node_id].items()
            if wanted is None or kind in wanted
            for edge in by_neighbour.values()
            if edge.confidence >= min_confidence
        ]

    # ------------------------------------------------------------------ traversal

    def forward_reachable(
        self,
        starts: NodeId | Iterable[NodeId],
        *,
        kinds: Iterable[EdgeKind] | None = None,
        min_confidence: Confidence = Confidence.UNKNOWN,
        max_depth: int | None = None,
    ) -> Reachability:
        """What ``starts`` depend on: follow edges from source to target."""
        return breadth_first(
            self._out, starts, kinds=kinds, min_confidence=min_confidence, max_depth=max_depth
        )

    def reverse_reachable(
        self,
        starts: NodeId | Iterable[NodeId],
        *,
        kinds: Iterable[EdgeKind] | None = None,
        min_confidence: Confidence = Confidence.UNKNOWN,
        max_depth: int | None = None,
    ) -> Reachability:
        """What depends on ``starts``: follow edges from target back to source."""
        return breadth_first(
            self._in, starts, kinds=kinds, min_confidence=min_confidence, max_depth=max_depth
        )

    def subgraph(self, node_ids: Iterable[NodeId]) -> "DependencyGraph":
        """The induced subgraph: the given nodes and every edge between two of them."""
        keep = sorted(set(node_ids))
        result = DependencyGraph()
        for node_id in keep:
            result.add_node(self.node(node_id))
        kept = frozenset(keep)
        for edge in self.edges():
            if edge.source in kept and edge.target in kept:
                result.add_edge(edge)
        return result

    # ------------------------------------------------------------------ serialization

    def to_dict(self) -> dict[str, JsonValue]:
        """Deterministic JSON-native form: nodes sorted by ID, edges by ``edge_sort_key``."""
        return {
            "format": GRAPH_FORMAT,
            "version": GRAPH_FORMAT_VERSION,
            "nodes": [to_dict(node) for node in self.nodes()],
            "edges": [to_dict(edge) for edge in self.edges()],
        }

    @classmethod
    def from_dict(cls, data: object) -> "DependencyGraph":
        """Strict inverse of :meth:`to_dict`. Unknown or missing keys, duplicate nodes, duplicate
        ``(source, kind, target)`` edges and dangling edges raise :class:`SerializationError`."""
        if not isinstance(data, dict):
            raise SerializationError("graph document must be an object")
        if set(data) != _DOCUMENT_KEYS:
            raise SerializationError(f"graph document keys must be {sorted(_DOCUMENT_KEYS)}")
        if data["format"] != GRAPH_FORMAT:
            raise SerializationError(f"graph format must be {GRAPH_FORMAT!r}")
        version = data["version"]
        if type(version) is not int or version != GRAPH_FORMAT_VERSION:
            raise SerializationError(f"graph version must be {GRAPH_FORMAT_VERSION}")
        nodes, edges = data["nodes"], data["edges"]
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise SerializationError("graph nodes and edges must be arrays")
        graph = cls()
        for item in nodes:
            node = from_dict(Node, item)
            if graph.has_node(node.id):
                raise SerializationError(f"duplicate node {node.id!r}")
            graph.add_node(node)
        for item in edges:
            edge = from_dict(Edge, item)
            if not (graph.has_node(edge.source) and graph.has_node(edge.target)):
                raise SerializationError(f"edge {edge.source!r} -> {edge.target!r} is dangling")
            if graph.edge(edge.source, edge.kind, edge.target) is not None:
                raise SerializationError(
                    f"duplicate edge {edge.source!r} -{edge.kind}-> {edge.target!r}"
                )
            graph.add_edge(edge)
        return graph

    # ------------------------------------------------------------------ comparison

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DependencyGraph):
            return NotImplemented
        return self.nodes() == other.nodes() and self.edges() == other.edges()

    def __repr__(self) -> str:
        return f"DependencyGraph(nodes={self.node_count}, edges={self.edge_count})"
