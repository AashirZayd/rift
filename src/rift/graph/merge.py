"""The duplicate-edge rule (SPEC §10, ADR-0009).

Two edges with the same ``(source, kind, target)`` are one dependency seen more than once. The
graph stores one edge for them.

An occurrence's **restricting flags** (``type_checking_only``, ``conditional``) say *when* it
holds. Occurrences are grouped by their restricting flags:

1. The group with the **fewest restrictions** wins. A runtime import subsumes a ``TYPE_CHECKING``
   import of the same module. Between equally many restrictions, the sorted flag names decide:
   ``conditional`` beats ``type_checking_only``, a runtime edge being the broader dependency.
2. Within the winning group, the edges merge:
   - ``confidence`` is the strongest;
   - ``origin`` comes from a strongest edge; on a tie, the origin declared first in
     :class:`EdgeOrigin` wins;
   - provenance flags (``star_import``, ``dynamic``: *how* the edge was found) are unioned;
   - ``evidence_ids`` are the sorted union.

Occurrences in losing groups are subsumed. Their confidence, provenance and evidence describe
another context, so carrying them over would claim more than any single occurrence shows. For
example, a ``certain`` ``TYPE_CHECKING`` import plus a ``likely`` dynamic runtime import is
stored as a ``likely`` runtime edge, never a ``certain`` one. The rule never hides a runtime
dependency behind ``type_checking_only`` and never inflates confidence.

It is a lexicographic choice followed by an associative, commutative merge, so the stored edge
never depends on insertion order.
"""

from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.core.models.graph import Edge, EdgeFlag, EdgeOrigin
from rift.graph.errors import GraphError

RESTRICTING_FLAGS = frozenset({EdgeFlag.TYPE_CHECKING_ONLY, EdgeFlag.CONDITIONAL})
_ORIGIN_ORDER = MappingProxyType({origin: index for index, origin in enumerate(EdgeOrigin)})


def restriction_key(edge: Edge) -> tuple[int, tuple[str, ...]]:
    """Smaller is less restricted: the number of restricting flags, then their sorted names."""
    names = tuple(sorted(flag.value for flag in edge.flags & RESTRICTING_FLAGS))
    return (len(names), names)


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
    first_key, second_key = restriction_key(first), restriction_key(second)
    if first_key != second_key:
        return first if first_key < second_key else second
    strongest = first if _strength(first) >= _strength(second) else second
    return Edge(
        source=first.source,
        target=first.target,
        kind=first.kind,
        origin=strongest.origin,
        confidence=strongest.confidence,
        evidence_ids=tuple(sorted({*first.evidence_ids, *second.evidence_ids})),
        flags=first.flags | second.flags,  # same restricting flags; provenance unioned
    )
