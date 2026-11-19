"""``--symbol`` resolution (SPEC §24 M4, ADR-0010 §9).

A query is matched in tiers; the first tier with any match decides:

1. **full name**: the node ID (``py:pkg.auth:Session.refresh``), ``module:qualname``,
   ``module.qualname``, a module's dotted name, or a test's pytest ID;
2. **qualname**: ``Session.refresh``;
3. **short name**: the last qualname segment, ``refresh``.

One match in that tier wins. Several matches are ambiguous: every candidate is returned, sorted,
and the caller must choose (the CLI exits 2, M7). No match in any tier raises
:class:`SymbolNotFoundError`.
"""

from dataclasses import dataclass

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.graph.multigraph import DependencyGraph
from rift.impact.errors import ImpactError, SymbolNotFoundError
from rift.impact.index import ProjectIndex


@dataclass(frozen=True, slots=True, kw_only=True)
class SymbolLookup:
    """``match`` is set when the query is unambiguous; ``candidates`` holds every node of the
    deciding tier, sorted (just ``match`` when unambiguous)."""

    query: str
    match: NodeId | None
    candidates: tuple[NodeId, ...]

    @property
    def is_ambiguous(self) -> bool:
        return self.match is None


def find_symbol(query: str, *, graph: DependencyGraph, index: ProjectIndex) -> SymbolLookup:
    """Resolve ``query`` against the analyzed module and symbol nodes in ``graph``."""
    text = query.strip() if isinstance(query, str) else ""
    if not text:
        raise ImpactError(
            "a symbol query must be a non-empty string", code=ErrorCode.INVALID_ARGUMENT
        )
    full: list[NodeId] = []
    by_qualname: list[NodeId] = []
    by_short: list[NodeId] = []
    for node in graph.nodes():
        if node.id.startswith("file:"):
            continue
        facts = index.facts(node.id)
        names = {str(node.id)}
        if node.id.startswith("test:"):
            names.add(node.id.removeprefix("test:"))
        if facts.symbol is None:
            if facts.module is not None:
                names.add(facts.module)
        else:
            qualname = facts.symbol.qualname
            names |= {f"{facts.module}:{qualname}", f"{facts.module}.{qualname}"}
            if qualname == text:
                by_qualname.append(node.id)
            if qualname.rsplit(".", 1)[-1] == text:
                by_short.append(node.id)
        if text in names:
            full.append(node.id)
    for tier in (full, by_qualname, by_short):
        if tier:
            ordered = tuple(sorted(set(tier)))
            match = ordered[0] if len(ordered) == 1 else None
            return SymbolLookup(query=text, match=match, candidates=ordered)
    raise SymbolNotFoundError(
        f"no analyzed symbol matches {text!r}",
        hint="use a dotted name (pkg.module.Class.method), a qualname, or a short name",
    )
