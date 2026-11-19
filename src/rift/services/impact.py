"""Impact use cases (SPEC §11, §19 ``rift impact``): git → analysis → graph → ImpactResult.

The head of a change is always the **working tree**. Analysis reads files from disk, so a
revision that is not checked out cannot be analyzed (ADR-0010 §2). The base defaults to config
``git.default_base``, then ``merge-base HEAD origin/HEAD`` (SPEC §8).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rift.analysis.python.analyzer import ProjectAnalysis
from rift.analysis.python.options import AnalysisOptions
from rift.core.ids import NodeId
from rift.core.models.change import ChangeSet
from rift.core.models.impact import ImpactResult
from rift.core.models.repo import Repository
from rift.git.changes import DEFAULT_RENAME_SIMILARITY, detect_changes
from rift.git.repository import open_repository
from rift.git.revisions import resolve_base
from rift.git.runner import DEFAULT_TIMEOUT
from rift.graph.builder import GraphBuild, build_graph
from rift.impact.assess import ImpactSettings, assess_impact
from rift.impact.index import ProjectIndex
from rift.impact.lookup import SymbolLookup, find_symbol
from rift.impact.mapping import ChangeMapping, map_changes, map_nodes, map_paths
from rift.services.analysis import analyze_repository


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactOptions:
    """The configuration impact uses (``rift.config`` file wiring is M7)."""

    analysis: AnalysisOptions = field(default_factory=AnalysisOptions)
    impact: ImpactSettings = field(default_factory=ImpactSettings)
    default_base: str | None = None
    rename_similarity: int = DEFAULT_RENAME_SIMILARITY
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeImpact:
    """A change set and what it could affect."""

    changes: ChangeSet
    impact: ImpactResult


@dataclass(frozen=True, slots=True)
class _Project:
    repo: Repository
    analysis: ProjectAnalysis
    build: GraphBuild


def _load(root: Path, options: ImpactOptions) -> _Project:
    repo = open_repository(root, timeout=options.timeout)
    analysis = analyze_repository(repo, options=options.analysis, timeout=options.timeout)
    return _Project(repo=repo, analysis=analysis, build=build_graph(analysis.results))


def _assess(project: _Project, mapping: ChangeMapping, options: ImpactOptions) -> ImpactResult:
    return assess_impact(
        analysis=project.analysis, build=project.build, mapping=mapping, settings=options.impact
    )


def impact_of_changes(
    root: Path, *, base: str | None = None, options: ImpactOptions | None = None
) -> ChangeImpact:
    """Impact of the changes from ``base`` to the working tree (staged, unstaged, untracked)."""
    opts = options or ImpactOptions()
    project = _load(root, opts)
    base_commit = resolve_base(
        project.repo, explicit=base, default_base=opts.default_base, timeout=opts.timeout
    )
    changes = detect_changes(
        project.repo,
        base=base_commit,
        rename_similarity=opts.rename_similarity,
        timeout=opts.timeout,
    )
    mapping = map_changes(changes.files, ProjectIndex(project.analysis))
    return ChangeImpact(changes=changes, impact=_assess(project, mapping, opts))


def resolve_symbol(root: Path, query: str, *, options: ImpactOptions | None = None) -> SymbolLookup:
    """``--symbol NAME``: the unique match, or every candidate when the name is ambiguous.
    Raises ``SymbolNotFoundError`` when nothing matches."""
    opts = options or ImpactOptions()
    project = _load(root, opts)
    return find_symbol(query, graph=project.build.graph, index=ProjectIndex(project.analysis))


def impact_of_nodes(
    root: Path, nodes: Iterable[NodeId], *, options: ImpactOptions | None = None
) -> ImpactResult:
    """Impact of a hypothetical change to exactly ``nodes`` (a resolved ``--symbol``)."""
    opts = options or ImpactOptions()
    project = _load(root, opts)
    return _assess(project, map_nodes(nodes, ProjectIndex(project.analysis)), opts)


def impact_of_paths(
    root: Path, paths: Iterable[str], *, options: ImpactOptions | None = None
) -> ImpactResult:
    """``rift impact PATH...``: impact of a hypothetical change to every line of ``paths``."""
    opts = options or ImpactOptions()
    project = _load(root, opts)
    return _assess(project, map_paths(paths, ProjectIndex(project.analysis)), opts)
