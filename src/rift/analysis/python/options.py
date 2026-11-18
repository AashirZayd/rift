"""Analyzer identity and options (SPEC §9, §20 ``[analysis]``)."""

import sys
from dataclasses import dataclass

from rift.core.errors import ErrorCode, RiftError

ANALYZER_NAME = "python"
# Bump whenever the analyzer's output for the same input can change (cache validity, SPEC §17).
ANALYZER_VERSION = "0.1.0"
DEFAULT_MAX_FILE_BYTES = 1_048_576  # SPEC §9.1 / §20 analysis.max_file_bytes


class AnalysisError(RiftError):
    """Invalid analyzer input (options, root, file list). Per-file problems are diagnostics."""

    default_code = ErrorCode.INVALID_ARGUMENT


def running_python_version() -> str:
    """``major.minor`` of this interpreter: parse results depend on it (SPEC §9.1)."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisOptions:
    """``source_roots=None`` means autodetect (``src`` if it holds importable code, else
    ``"."``). ``exclude`` holds repo-relative glob patterns (ADR-0008)."""

    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    source_roots: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_file_bytes, int)
            or isinstance(self.max_file_bytes, bool)
            or self.max_file_bytes < 0
        ):
            raise AnalysisError("max_file_bytes must be a non-negative integer")
        if self.source_roots is not None and not self.source_roots:
            raise AnalysisError("source_roots must be None (autodetect) or non-empty")
