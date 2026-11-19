"""SPEC §11.1 classification (ADR-0010 §7). Tiers are ordinal; nothing here is a probability.

Test code is left out throughout; related tests are reported separately.

- **Crossing dependency**: an edge ``s -> t`` of a traversed kind, at least ``min_confidence``,
  where both ends are in the impact set (changed or affected), neither is test code, and they lie
  in different modules. ``s`` depends on ``t``, and ``t`` is impacted, so the change reaches
  across a module boundary. Edges between two changed nodes count too: changing a helper *and*
  its caller elsewhere still crosses a boundary.
- **unit**: a node's top-level package; a module-less file is its own unit.

The highest matching tier wins:

- ``public_api``:
  - a changed or removed symbol is public (ADR-0010 §8) and its unit is a public package; or
  - a removed module path is public in a public package: a deleted module, a module renamed
    away, or a deleted file whose base side could not be parsed. Every ``import`` of that path
    breaks, whatever the module defined (a re-export shim has no public symbols of its own).
- ``cross_package``: the crossing dependencies' ends span two or more units.
- ``module``: some crossing dependency exists (one unit).
- ``local``: no crossing dependency: everything stays inside the changed nodes' own modules.
- ``none``: no changed nodes at all.

The tier never decreases when more nodes change: the impact set and its crossing dependencies
only grow.
"""

from collections.abc import Collection, Iterable

from rift.core.ids import NodeId
from rift.core.models.graph import Confidence, EdgeKind
from rift.core.models.impact import ImpactClass
from rift.graph.multigraph import DependencyGraph
from rift.impact.index import ProjectIndex

TRAVERSED_KINDS = frozenset(
    {
        EdgeKind.IMPORTS,
        EdgeKind.CALLS,
        EdgeKind.INHERITS,
        EdgeKind.REFERENCES,
        EdgeKind.DEPENDS_ON,
    }
)


def classify(  # noqa: PLR0913  # the inputs of one §11.1 decision, keyword-only
    *,
    changed: Iterable[NodeId],
    impact_set: Collection[NodeId],
    graph: DependencyGraph,
    index: ProjectIndex,
    public_units: Collection[str],
    min_confidence: Confidence,
) -> ImpactClass:
    """The §11.1 tier of a change (see the module docstring for the exact rules)."""
    changed_facts = [index.facts(node) for node in changed]
    if not changed_facts:
        return ImpactClass.NONE
    for fact in changed_facts:
        if fact.is_test_code or fact.unit not in public_units:
            continue
        if fact.is_public or (fact.removed and fact.symbol is None and fact.public_path):
            return ImpactClass.PUBLIC_API
    members = frozenset(impact_set)
    units: set[str] = set()
    for source in sorted(members):
        source_facts = index.facts(source)
        if source_facts.is_test_code or not graph.has_node(source):
            continue
        for edge in graph.successors(source, kinds=TRAVERSED_KINDS, min_confidence=min_confidence):
            if edge.target not in members:
                continue
            target_facts = index.facts(edge.target)
            if not target_facts.is_test_code and target_facts.module_key != source_facts.module_key:
                units.update((source_facts.unit, target_facts.unit))
    if not units:
        return ImpactClass.LOCAL
    return ImpactClass.CROSS_PACKAGE if len(units) >= 2 else ImpactClass.MODULE  # noqa: PLR2004  # "two or more units"
