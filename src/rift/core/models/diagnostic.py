"""Structured diagnostics: problems a subsystem hit while doing its work (SPEC §9.1, §17, §18)."""

import re
from dataclasses import dataclass
from enum import StrEnum

from rift.core.models._checks import check_types, non_empty, require
from rift.core.models.source import SourceLocation

_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """One problem report. ``code`` is a stable snake_case identifier owned by the producer
    (ADR-0007); ``message`` is human text and never contains absolute paths or environment
    values. ``subject`` names what the diagnostic is about (usually a repo path)."""

    severity: DiagnosticSeverity
    code: str
    message: str
    subject: str | None
    location: SourceLocation | None

    def __post_init__(self) -> None:
        check_types(self)
        require(bool(_CODE.match(self.code)), f"diagnostic code must be snake_case: {self.code!r}")
        non_empty(self.message, "message")
        if self.subject is not None:
            non_empty(self.subject, "subject")
