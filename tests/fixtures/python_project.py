"""Builder for fixture Python projects in ``tmp_path`` (real files; nothing is committed).

Sources are dedented, so tests can write them inline. Helpers flatten analysis output into
plain tuples that read well in assertions.
"""

import textwrap
from collections.abc import Mapping
from pathlib import Path

from rift.analysis.python.analyzer import ProjectAnalysis, analyze_project
from rift.analysis.python.options import AnalysisOptions
from rift.core.models.analysis import AnalysisResult


def write_project(root: Path, files: Mapping[str, str | bytes]) -> Path:
    """Write ``{repo_path: source}``; ``str`` sources are dedented and UTF-8 encoded."""
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (
            content
            if isinstance(content, bytes)
            else textwrap.dedent(content).lstrip("\n").encode("utf-8")
        )
        target.write_bytes(data)
    return root


def analyze(
    root: Path, files: Mapping[str, str | bytes], **options: object
) -> dict[str, AnalysisResult]:
    """Write the project, analyze it, and index the results by path."""
    project = analyze_tree(write_project(root, files), **options)
    return {r.file_path: r for r in project.results}


def analyze_tree(root: Path, **options: object) -> ProjectAnalysis:
    return analyze_project(root, AnalysisOptions(**options))  # type: ignore[arg-type]


def edges(result: AnalysisResult) -> set[tuple[str, str, str, str, frozenset[str]]]:
    """``{(source, kind, target, confidence, flags)}``."""
    return {
        (e.source, e.kind.value, e.target, e.confidence.value, frozenset(f.value for f in e.flags))
        for e in result.edges
    }


def edge_targets(result: AnalysisResult, kind: str) -> set[tuple[str, str, str]]:
    """``{(source, target, confidence)}`` for one edge kind."""
    return {(e.source, e.target, e.confidence.value) for e in result.edges if e.kind.value == kind}


def unresolved(result: AnalysisResult) -> set[tuple[str, str]]:
    """``{(reason, raw)}``."""
    return {(u.reason.value, u.raw) for u in result.unresolved}


def symbols(result: AnalysisResult) -> dict[str, tuple[str, str]]:
    """``{qualname: (kind, visibility)}`` (the module symbol is keyed by its module name)."""
    return {s.qualname: (s.kind.value, s.visibility.value) for s in result.symbols}
