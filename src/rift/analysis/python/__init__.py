"""Python analysis: static extraction and resolution of Python source with the stdlib ast module.

Public entry points: :func:`analyze_project` and :func:`analyze_files`. Analyzed code is never
imported, executed or compiled to bytecode (SPEC §9, §21).
"""

from rift.analysis.python.analyzer import ProjectAnalysis, analyze_files, analyze_project
from rift.analysis.python.options import (
    ANALYZER_NAME,
    ANALYZER_VERSION,
    DEFAULT_MAX_FILE_BYTES,
    AnalysisError,
    AnalysisOptions,
)

__all__ = [
    "ANALYZER_NAME",
    "ANALYZER_VERSION",
    "DEFAULT_MAX_FILE_BYTES",
    "AnalysisError",
    "AnalysisOptions",
    "ProjectAnalysis",
    "analyze_files",
    "analyze_project",
]
