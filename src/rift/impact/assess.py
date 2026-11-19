"""SPEC §11 steps 2-7: from changed nodes to an :class:`ImpactResult` (ADR-0010).

2. Reverse traversal over ``imports | calls | inherits | references | depends_on`` from the
   changed nodes, honouring ``min_confidence`` (default ``possible``) and ``max_depth``.
   References that no longer resolve but name a changed or removed module link their source to
   that module's changed node (``likely``, ADR-0010 §5). That is how deleting or renaming a module
   or symbol reaches the code that used it.
3. Public interfaces among the changed and affected nodes.
4. Related tests: ``test`` nodes reached (changed tests included). There are no ``tests`` edges
   yet, since test linking is M5.
5. Unresolved references whose source is in the impact set.
6. Classification (:mod:`rift.impact.classify`), plus completeness with every reason listed.
7. An explanation path for every affected node and related test.
"""

from dataclasses import dataclass, field

from rift.analysis.python.analyzer import ProjectAnalysis
from rift.core.models.analysis import limitation_sort_key, sort_unique, unresolved_sort_key
from rift.core.models.diagnostic import DiagnosticSeverity
from rift.core.models.evidence import Limitation
from rift.core.models.graph import (
    Confidence,
    Edge,
    EdgeKind,
    EdgeOrigin,
    Node,
    NodeKind,
    UnresolvedReference,
)
from rift.core.models.impact import Completeness, ImpactExplanation, ImpactResult
from rift.graph.builder import GraphBuild
from rift.graph.multigraph import DependencyGraph
from rift.impact.classify import classify
from rift.impact.errors import ImpactError
from rift.impact.index import ProjectIndex
from rift.impact.mapping import ChangeMapping, ModuleAnchor

TRAVERSED_KINDS = frozenset(
    {
        EdgeKind.IMPORTS,
        EdgeKind.CALLS,
        EdgeKind.INHERITS,
        EdgeKind.REFERENCES,
        EdgeKind.DEPENDS_ON,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactSettings:
    """``impact.min_confidence`` / ``impact.max_depth`` and ``project.public_packages``
    (``None``: every top-level package of non-test code)."""

    min_confidence: Confidence = Confidence.POSSIBLE
    max_depth: int | None = None
    public_packages: tuple[str, ...] | None = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.min_confidence, Confidence):
            raise ImpactError("min_confidence must be a Confidence")
        if self.max_depth is not None and (type(self.max_depth) is not int or self.max_depth < 0):
            raise ImpactError("max_depth must be a non-negative int or None")


def _names(raw: str, module: str) -> bool:
    return raw == module or raw.startswith(module + ".")


def _anchor_for(raw: str, anchors: tuple[ModuleAnchor, ...]) -> ModuleAnchor | None:
    """The most specific module anchor named by ``raw`` (``pkg.m.f`` prefers ``pkg.m`` to
    ``pkg``)."""
    named = [a for a in anchors if _names(raw, a.module)]
    return max(named, key=lambda a: (len(a.module), a.module), default=None)


def _working_graph(build: GraphBuild, mapping: ChangeMapping) -> DependencyGraph:
    """The graph plus one ``likely`` reference per unresolved reference or dangling edge that
    names a changed or removed module. The input graph is never modified."""
    links: list[Edge] = []
    for ref in build.unresolved:
        anchor = _anchor_for(ref.raw, mapping.anchors)
        if anchor is not None and ref.source != anchor.anchor:
            links.append(
                Edge(
                    source=ref.source,
                    target=anchor.anchor,
                    kind=EdgeKind.REFERENCES,
                    origin=EdgeOrigin.NAME_RESOLUTION,
                    confidence=Confidence.LIKELY,
                    evidence_ids=(),
                    flags=frozenset(),
                )
            )
    for dangling in build.dangling:
        target_module = dangling.target.removeprefix("py:").split(":", 1)[0]
        anchor = _anchor_for(target_module, mapping.anchors)
        if anchor is not None and dangling.source != anchor.anchor:
            links.append(
                Edge(
                    source=dangling.source,
                    target=anchor.anchor,
                    kind=dangling.kind,
                    origin=dangling.origin,
                    confidence=dangling.confidence,
                    evidence_ids=dangling.evidence_ids,
                    flags=dangling.flags,
                )
            )
    missing = [n for n in mapping.changed_nodes if not build.graph.has_node(n)]
    if not links and not missing:
        return build.graph
    graph = DependencyGraph.from_dict(build.graph.to_dict())
    for node in missing:  # a deleted file's node
        if node.startswith("file:"):
            graph.add_node(Node(id=node, kind=NodeKind.FILE, label=node[5:], location=None))
    for link in sorted(links, key=lambda e: (e.source, e.target, e.kind.value)):
        if graph.has_node(link.source) and graph.has_node(link.target):
            graph.add_edge(link)
    return graph


def _analysis_limitations(analysis: ProjectAnalysis) -> list[Limitation]:
    """Every file RIFT could not analyze: a parse or decode failure, or a skipped file."""
    found = list(analysis.limitations)
    for result in analysis.results:
        found.extend(result.limitations)
        failures = sorted(
            {d.code for d in result.diagnostics if d.severity is DiagnosticSeverity.ERROR}
        )
        if failures:
            found.append(
                Limitation(
                    kind="analysis_failed",
                    message=f"file could not be analyzed ({', '.join(failures)})",
                    subject=result.file_path,
                )
            )
    return found


def _unresolved_limitations(refs: list[UnresolvedReference]) -> list[Limitation]:
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.reason.value] = counts.get(ref.reason.value, 0) + 1
    return [
        Limitation(
            kind="unresolved_reference",
            message=f"{count} unresolved reference(s) with reason {reason} touch the impact set",
            subject=reason,
        )
        for reason, count in sorted(counts.items())
    ]


def assess_impact(
    *,
    analysis: ProjectAnalysis,
    build: GraphBuild,
    mapping: ChangeMapping,
    settings: ImpactSettings | None = None,
) -> ImpactResult:
    """What ``mapping``'s changed nodes could affect (SPEC §11)."""
    opts = settings or ImpactSettings()
    index = ProjectIndex(analysis)
    graph = _working_graph(build, mapping)
    changed = mapping.changed_nodes
    starts = [node for node in changed if graph.has_node(node)]
    reach = graph.reverse_reachable(
        starts, kinds=TRAVERSED_KINDS, min_confidence=opts.min_confidence, max_depth=opts.max_depth
    )
    changed_set = frozenset(changed)
    affected = tuple(node for node in reach.nodes if node not in changed_set)
    related = tuple(node for node in reach.nodes if node.startswith("test:"))
    impact_set = changed_set | frozenset(reach.nodes)

    unresolved = sort_unique(
        [u for u in build.unresolved if u.source in impact_set], unresolved_sort_key
    )
    limitations = sort_unique(
        [
            *mapping.limitations,
            *_analysis_limitations(analysis),
            *_unresolved_limitations(list(unresolved)),
        ],
        limitation_sort_key,
    )
    public_units = frozenset(
        opts.public_packages if opts.public_packages is not None else index.public_units()
    )
    public = tuple(
        node
        for node in sorted(impact_set)
        if (facts := index.facts(node)).is_symbol and facts.is_public and facts.unit in public_units
    )
    explained = sorted(set(affected) | set(related))
    return ImpactResult(
        changed_nodes=changed,
        affected_nodes=affected,
        public_interfaces=public,
        related_tests=related,
        unresolved=unresolved,
        classification=classify(
            changed=changed, affected=affected, index=index, public_units=public_units
        ),
        completeness=Completeness.PARTIAL if limitations else Completeness.COMPLETE,
        limitations=limitations,
        explanations=tuple(ImpactExplanation(node=n, path=reach.path(n)) for n in explained),
    )
