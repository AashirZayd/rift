"""Run the impact engine on an in-memory fixture project (real analyzer and graph, no git)."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from rift.analysis.python.analyzer import ProjectAnalysis, analyze_project
from rift.analysis.python.options import AnalysisOptions
from rift.core.ids import NodeId
from rift.core.models.change import ChangedFile, ChangeStatus, LineRange
from rift.core.models.impact import ImpactResult
from rift.graph.builder import build_graph
from rift.impact.assess import ImpactSettings, assess_impact
from rift.impact.index import ProjectIndex
from rift.impact.mapping import map_changes, map_nodes
from tests.fixtures.python_project import write_project


def analyze(root: Path, files: Mapping[str, str | bytes], **options: object) -> ProjectAnalysis:
    return analyze_project(write_project(root, files), AnalysisOptions(**options))  # type: ignore[arg-type]  # test helper


def modified(path: str, *lines: int) -> ChangedFile:
    return ChangedFile(
        path=path,
        old_path=None,
        status=ChangeStatus.MODIFIED,
        changed_lines=tuple(LineRange(start=n, end=n) for n in lines),
        is_binary=False,
    )


def impact_of_change(
    analysis: ProjectAnalysis,
    files: Sequence[ChangedFile],
    settings: ImpactSettings | None = None,
) -> ImpactResult:
    mapping = map_changes(files, ProjectIndex(analysis))
    return assess_impact(
        analysis=analysis, build=build_graph(analysis.results), mapping=mapping, settings=settings
    )


def impact_of_nodes(
    analysis: ProjectAnalysis, nodes: Sequence[str], settings: ImpactSettings | None = None
) -> ImpactResult:
    mapping = map_nodes([NodeId(n) for n in nodes], ProjectIndex(analysis))
    return assess_impact(
        analysis=analysis, build=build_graph(analysis.results), mapping=mapping, settings=settings
    )


def explanation(result: ImpactResult, node: str) -> tuple[str, ...]:
    return next(tuple(e.path) for e in result.explanations if e.node == node)
