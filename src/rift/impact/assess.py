"""SPEC §11 steps 2-7: from changed nodes to an :class:`ImpactResult` (ADR-0010).

2. Reverse traversal over ``imports | calls | inherits | references | depends_on`` from the
   mapping's starts, honouring ``min_confidence`` (default ``possible``) and ``max_depth``.
   References that no longer resolve but name a changed or removed module or symbol link their
   source to that anchor (``likely``, ADR-0010 §5). A failed module-level import also links every
   symbol of the importing file: the whole module fails to import, tests included.
3. Public interfaces among the changed and affected nodes.
4. Related tests: ``test`` nodes reached (changed tests included). There are no ``tests`` edges
   yet, since test linking is M5.
5. Unresolved references whose source is in the impact set.
6. Classification (:mod:`rift.impact.classify`), plus completeness with every reason listed.
7. An explanation path for every affected node and related test.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from rift.analysis.python.analyzer import LIMITATION_OVERSIZED, ProjectAnalysis
from rift.core.ids import NodeId
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
from rift.core.models.source import SymbolKind
from rift.discovery.files import (
    LIMITATION_OUTSIDE_ROOT,
    LIMITATION_SYMLINK,
    LIMITATION_UNREADABLE,
    LIMITATION_UNREPRESENTABLE,
)
from rift.graph.builder import GraphBuild, pytest_node_of
from rift.graph.multigraph import DependencyGraph
from rift.impact.classify import TRAVERSED_KINDS, classify
from rift.impact.errors import ImpactError
from rift.impact.index import NodeFacts, ProjectIndex
from rift.impact.mapping import ChangeMapping

LIMITATION_ANALYSIS_FAILED = "analysis_failed"
LIMITATION_UNRESOLVED = "unresolved_reference"
# A skipped file was never analyzed: its dependencies are unknown wherever it is.
_SKIPS = frozenset(
    {
        LIMITATION_OVERSIZED,
        LIMITATION_SYMLINK,
        LIMITATION_UNREADABLE,
        LIMITATION_UNREPRESENTABLE,
        LIMITATION_OUTSIDE_ROOT,
    }
)
_NODE_KINDS: Mapping[SymbolKind, NodeKind] = MappingProxyType(
    {
        SymbolKind.CLASS: NodeKind.CLASS,
        SymbolKind.FUNCTION: NodeKind.FUNCTION,
        SymbolKind.METHOD: NodeKind.METHOD,
        SymbolKind.CONSTANT: NodeKind.CONSTANT,
        SymbolKind.TEST_FUNCTION: NodeKind.FUNCTION,
        SymbolKind.TEST_METHOD: NodeKind.METHOD,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactSettings:
    """``impact.min_confidence`` / ``impact.max_depth`` and ``project.public_packages``
    (``None``: every top-level package of non-test code under the source roots)."""

    min_confidence: Confidence = Confidence.POSSIBLE
    max_depth: int | None = None
    public_packages: tuple[str, ...] | None = field(default=None)

    def __post_init__(self) -> None:
        if not isinstance(self.min_confidence, Confidence):
            raise ImpactError("min_confidence must be a Confidence")
        if self.max_depth is not None and (type(self.max_depth) is not int or self.max_depth < 0):
            raise ImpactError("max_depth must be a non-negative int or None")
        if self.public_packages is not None and (
            not isinstance(self.public_packages, tuple)
            or not all(isinstance(p, str) and p.isidentifier() for p in self.public_packages)
        ):
            raise ImpactError("public_packages must be None or a tuple of top-level package names")


def _link(source: NodeId, target: NodeId, *, like: Edge | None = None) -> Edge:
    """A ``likely`` reference from ``source`` to an anchor, or a copy of a dangling edge."""
    if like is not None:
        return Edge(
            source=source,
            target=target,
            kind=like.kind,
            origin=like.origin,
            confidence=like.confidence,
            evidence_ids=like.evidence_ids,
            flags=like.flags,
        )
    return Edge(
        source=source,
        target=target,
        kind=EdgeKind.REFERENCES,
        origin=EdgeOrigin.NAME_RESOLUTION,
        confidence=Confidence.LIKELY,
        evidence_ids=(),
        flags=frozenset(),
    )


def _anchor_for(dotted: str, anchors: Mapping[str, NodeId]) -> NodeId | None:
    """The most specific anchor naming ``dotted`` or one of its dotted prefixes."""
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        found = anchors.get(".".join(parts[:end]))
        if found is not None:
            return found
    return None


def _removed_node(fact: NodeFacts) -> Node:
    if fact.symbol is None:
        return Node(id=fact.node, kind=NodeKind.FILE, label=fact.path, location=None)
    kind = NodeKind.TEST if fact.node.startswith("test:") else _NODE_KINDS[fact.symbol.kind]
    return Node(id=fact.node, kind=kind, label=fact.symbol.qualname, location=fact.symbol.location)


def _file_symbols(index: ProjectIndex, path: str) -> list[NodeId]:
    return [
        pytest_node_of(s) or NodeId(s.id)
        for s in index.results[path].symbols
        if s.kind is not SymbolKind.MODULE
    ]


def _working_graph(
    build: GraphBuild, mapping: ChangeMapping, index: ProjectIndex
) -> DependencyGraph:
    """The graph plus the base-side (removed) nodes and the anchor links. The build's graph is
    never modified."""
    anchors = {a.name: a.anchor for a in mapping.anchors}
    links: list[Edge] = []
    for ref in build.unresolved:
        anchor = _anchor_for(ref.raw, anchors)
        if anchor is None or ref.source == anchor:
            continue
        links.append(_link(ref.source, anchor))
        source = index.facts(ref.source) if index.knows(ref.source) else None
        if source is not None and source.symbol is None and source.path in index.results:
            # A failed module-level import: the whole module fails, so all its code is affected.
            links.extend(_link(node, anchor) for node in _file_symbols(index, source.path))
    for dangling in build.dangling:
        prefix, _, rest = dangling.target.partition(":")
        if prefix != "py":
            continue
        anchor = _anchor_for(rest.replace(":", "."), anchors)
        if anchor is not None and dangling.source != anchor:
            links.append(_link(dangling.source, anchor, like=dangling))
    if not links and not mapping.removed:
        return build.graph
    graph = build.graph.copy()
    for fact in mapping.removed:
        if not graph.has_node(fact.node):
            graph.add_node(_removed_node(fact))
    for link in sorted(links, key=lambda e: (e.source, e.target, e.kind.value)):
        if (
            link.source != link.target
            and graph.has_node(link.source)
            and graph.has_node(link.target)
        ):
            graph.add_edge(link)
    return graph


def _analysis_limitations(
    analysis: ProjectAnalysis, impact_files: frozenset[str]
) -> list[Limitation]:
    """Files RIFT could not analyze (a parse or decode failure, or a skip) count wherever they
    are. Other analyzer limitations count only for files in the impact set."""
    found = list(analysis.limitations)  # discovery skips
    for result in analysis.results:
        found.extend(
            lim
            for lim in result.limitations
            if lim.kind in _SKIPS or result.file_path in impact_files
        )
        failures = sorted(
            {d.code for d in result.diagnostics if d.severity is DiagnosticSeverity.ERROR}
        )
        if failures:
            found.append(
                Limitation(
                    kind=LIMITATION_ANALYSIS_FAILED,
                    message=f"file could not be analyzed ({', '.join(failures)})",
                    subject=result.file_path,
                )
            )
    return found


def _unresolved_limitations(refs: tuple[UnresolvedReference, ...]) -> list[Limitation]:
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.reason.value] = counts.get(ref.reason.value, 0) + 1
    return [
        Limitation(
            kind=LIMITATION_UNRESOLVED,
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
    index: ProjectIndex | None = None,
) -> ImpactResult:
    """What ``mapping``'s changed nodes could affect (SPEC §11)."""
    opts = settings or ImpactSettings()
    facts = index if index is not None else ProjectIndex(analysis)
    for removed in mapping.removed:
        facts.register(removed)
    graph = _working_graph(build, mapping, facts)
    changed = mapping.changed_nodes
    starts = [node for node in mapping.starts if graph.has_node(node)]
    reach = graph.reverse_reachable(
        starts, kinds=TRAVERSED_KINDS, min_confidence=opts.min_confidence, max_depth=opts.max_depth
    )
    changed_set = frozenset(changed)
    affected = tuple(node for node in reach.nodes if node not in changed_set)
    impact_set = changed_set | frozenset(reach.nodes)
    related = tuple(sorted(node for node in impact_set if node.startswith("test:")))

    unresolved = sort_unique(
        [u for u in build.unresolved if u.source in impact_set], unresolved_sort_key
    )
    impact_files = frozenset(facts.facts(node).path for node in impact_set)
    limitations = sort_unique(
        [
            *mapping.limitations,
            *_analysis_limitations(analysis, impact_files),
            *_unresolved_limitations(unresolved),
        ],
        limitation_sort_key,
    )
    public_units = frozenset(
        opts.public_packages if opts.public_packages is not None else facts.public_units()
    )
    public = tuple(
        node
        for node in sorted(impact_set)
        if (fact := facts.facts(node)).is_public and fact.unit in public_units
    )
    explained = sorted(set(affected) | set(related))
    # A changed node that starts nothing (e.g. a test in a module whose import line changed)
    # explains itself.
    paths = {node: reach.path(node) if node in reach else (node,) for node in explained}
    return ImpactResult(
        changed_nodes=changed,
        affected_nodes=affected,
        public_interfaces=public,
        related_tests=related,
        unresolved=unresolved,
        classification=classify(
            changed=changed,
            impact_set=impact_set,
            graph=graph,
            index=facts,
            public_units=public_units,
            min_confidence=opts.min_confidence,
        ),
        completeness=Completeness.PARTIAL if limitations else Completeness.COMPLETE,
        limitations=limitations,
        explanations=tuple(ImpactExplanation(node=n, path=paths[n]) for n in explained),
    )
