"""SPEC §11.1 classification (ADR-0010 §7). Tiers are ordinal; nothing here is a probability.

Definitions (test code is left out throughout; related tests are reported separately):
- C: changed nodes that are not test code. S: the symbols among them.
- D_out: reverse dependents (affected, not changed) that are not test code and lie outside every
  module of C ("their own module"). A module-less file is its own module.
- unit: a node's top-level package; a module-less file is its own unit.

The highest matching tier wins:

- ``public_api``: some s in S is public (no private segment in its module or qualname) and its
  unit is a public package (default: every top-level package of non-test code).
- ``cross_package``: D_out is non-empty and C together with D_out spans two or more units.
- ``module``: D_out is non-empty (one unit).
- ``local``: D_out is empty: every dependent is inside the changed nodes' own modules. That
  includes the §11.1 rule "all changed symbols are private ..." and the leftover case of a
  public symbol outside the public packages with no outside dependents: the change stays in its
  module.
- ``none``: no changed file maps to an analyzable node (no changed nodes at all).
"""

from collections.abc import Collection, Iterable

from rift.core.ids import NodeId
from rift.core.models.impact import ImpactClass
from rift.impact.index import NodeFacts, ProjectIndex


def _module_key(facts: NodeFacts) -> str:
    return facts.module if facts.module is not None else f"file:{facts.path}"


def classify(
    *,
    changed: Iterable[NodeId],
    affected: Iterable[NodeId],
    index: ProjectIndex,
    public_units: Collection[str],
) -> ImpactClass:
    """The §11.1 tier of a change (see the module docstring for the exact rules)."""
    changed_facts = [index.facts(node) for node in changed]
    if not changed_facts:
        return ImpactClass.NONE
    code = [f for f in changed_facts if not f.is_test_code]
    if any(f.is_symbol and f.is_public and f.unit in public_units for f in code):
        return ImpactClass.PUBLIC_API
    own_modules = {_module_key(f) for f in code}
    outside = [
        f
        for f in (index.facts(node) for node in affected)
        if not f.is_test_code and _module_key(f) not in own_modules
    ]
    if not outside:
        return ImpactClass.LOCAL
    units = {f.unit for f in code} | {f.unit for f in outside}
    return ImpactClass.CROSS_PACKAGE if len(units) >= 2 else ImpactClass.MODULE  # noqa: PLR2004  # "two or more units"
