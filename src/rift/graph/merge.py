"""The duplicate-edge rule (SPEC §10, ADR-0009).

Two edges with the same ``(source, kind, target)`` are one dependency seen twice. They merge into
one edge:

- ``confidence``: the strongest;
- ``origin``: the origin of a strongest edge; on a tie, the one declared first in
  :class:`EdgeOrigin`;
- ``evidence_ids``: the sorted union;
- ``flags`` by meaning:
  - provenance flags (``star_import``, ``dynamic``: *how* the edge was found) are unioned;
  - restricting flags (``type_checking_only``, ``conditional``: *when* the edge holds) are
    intersected. The merged edge is restricted only if every occurrence is. A runtime import
    plus a ``TYPE_CHECKING`` import of the same module is a runtime dependency; marking it
    ``type_checking_only`` would hide impact.

The rule is commutative and associative, so the merged edge never depends on insertion order.
"""

from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.models.graph import Edge, EdgeFlag, EdgeOrigin
from rift.graph.errors import GraphError

RESTRICTING_FLAGS = frozenset({EdgeFlag.TYPE_CHECKING_ONLY, EdgeFlag.CONDITIONAL})
_ORIGIN_ORDER = MappingProxyType({origin: index for index, origin in enumerate(EdgeOrigin)})


def _strength(edge: Edge) -> tuple[int, int]:
    """Larger is stronger: confidence first, then the earlier-declared origin."""
    return (edge.confidence.rank, -_ORIGIN_ORDER[edge.origin])


def merge_edges(first: Edge, second: Edge) -> Edge:
    """Merge two occurrences of one ``(source, kind, target)`` dependency."""
    if (first.source, first.kind, first.target) != (second.source, second.kind, second.target):
        msg = "merge_edges needs edges with the same source, kind and target"
        raise GraphError(msg, code=ErrorCode.GRAPH_CONFLICT)
    if first == second:
        return first
    strongest = first if _strength(first) >= _strength(second) else second
    restricting = (first.flags & second.flags) & RESTRICTING_FLAGS
    provenance = (first.flags | second.flags) - RESTRICTING_FLAGS
    return Edge(
        source=first.source,
        target=first.target,
        kind=first.kind,
        origin=strongest.origin,
        confidence=strongest.confidence,
        evidence_ids=tuple(sorted({*first.evidence_ids, *second.evidence_ids})),
        flags=frozenset(restricting | provenance),
    )
