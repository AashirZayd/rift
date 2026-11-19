"""The duplicate-edge rule (SPEC §10, ADR-0009 §6).

An edge's identity is its **edge key**: ``(source, kind, target, restricting flags)``. The
restricting flags (``type_checking_only``, ``conditional``) say *when* the edge holds, so they are
part of what the edge claims:
- a ``certain`` ``TYPE_CHECKING`` import and a ``likely`` runtime import of the same module are
  two different edges, and the graph keeps both, in parallel;
- two edges with the same key are one dependency seen twice, and merge into one edge:
  - ``confidence``: the strongest;
  - ``origin``: from a strongest edge; on a tie, the one declared first in :class:`EdgeOrigin`;
  - provenance flags (``star_import``, ``dynamic``: *how* the edge was found): the union;
  - ``evidence_ids``: the sorted union.

The merge is commutative and associative, so the stored edge never depends on insertion order.
"""

from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.graph import Edge, EdgeFlag, EdgeKind, EdgeOrigin
from rift.graph.errors import GraphError

RESTRICTING_FLAGS = frozenset({EdgeFlag.TYPE_CHECKING_ONLY, EdgeFlag.CONDITIONAL})
_ORIGIN_ORDER = MappingProxyType({origin: index for index, origin in enumerate(EdgeOrigin)})

EdgeKey = tuple[NodeId, EdgeKind, NodeId, frozenset[EdgeFlag]]


def restrictions(edge: Edge) -> frozenset[EdgeFlag]:
    """The edge's restricting flags (part of its key)."""
    return edge.flags & RESTRICTING_FLAGS


def edge_key(edge: Edge) -> EdgeKey:
    """``(source, kind, target, restricting flags)``: the identity duplicate edges share."""
    return (edge.source, edge.kind, edge.target, restrictions(edge))


def restriction_key(flags: frozenset[EdgeFlag]) -> tuple[int, tuple[str, ...]]:
    """Order of restriction sets, least restricted first: the count, then the sorted names."""
    names = tuple(sorted(flag.value for flag in flags & RESTRICTING_FLAGS))
    return (len(names), names)


def _strength(edge: Edge) -> tuple[int, int]:
    """Larger is stronger: confidence first, then the earlier-declared origin."""
    return (edge.confidence.rank, -_ORIGIN_ORDER[edge.origin])


def merge_edges(first: Edge, second: Edge) -> Edge:
    """Merge two occurrences of one edge key."""
    if edge_key(first) != edge_key(second):
        msg = "merge_edges needs edges with the same source, kind, target and restricting flags"
        raise GraphError(msg, code=ErrorCode.GRAPH_CONFLICT)
    if first == second:
        return first
    strongest = first if _strength(first) >= _strength(second) else second
    return Edge(
        source=first.source,
        target=first.target,
        kind=first.kind,
        origin=strongest.origin,
        confidence=strongest.confidence,
        evidence_ids=tuple(sorted({*first.evidence_ids, *second.evidence_ids})),
        flags=first.flags | second.flags,  # equal restricting flags; provenance unioned
    )
