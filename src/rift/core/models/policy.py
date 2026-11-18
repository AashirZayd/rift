"""Invariants, requirements and policy results (SPEC §6.4, §14, §15). Evaluation is M6."""

import re
from dataclasses import dataclass
from enum import StrEnum

from rift.core.ids import EvidenceId, validate_evidence_id
from rift.core.models._checks import check_types, non_empty, require, sorted_unique

INVARIANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class RequirementLevel(StrEnum):
    MANDATORY = "mandatory"
    OPTIONAL = "optional"


class RequirementStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class Verdict(StrEnum):
    PASS = "pass"  # noqa: S105  # a verdict value, not a password
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True, kw_only=True)
class Scope:
    """Glob patterns over repo paths and/or symbol ID patterns (e.g. ``py:app.auth:*``)."""

    paths: tuple[str, ...]
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        check_types(self)
        for pattern in (*self.paths, *self.symbols):
            non_empty(pattern, "pattern")


@dataclass(frozen=True, slots=True, kw_only=True)
class Requirement:
    """``kind`` is a plain string: unknown kinds must survive to be reported ``unsupported``."""

    id: str
    kind: str
    level: RequirementLevel
    select: Scope | None

    def __post_init__(self) -> None:
        check_types(self)
        non_empty(self.id, "id")
        non_empty(self.kind, "kind")


@dataclass(frozen=True, slots=True, kw_only=True)
class Invariant:
    id: str
    description: str
    scope: Scope
    level: RequirementLevel
    requires: tuple[Requirement, ...]

    def __post_init__(self) -> None:
        check_types(self)
        require(
            isinstance(self.id, str) and INVARIANT_ID.fullmatch(self.id) is not None,
            f"invariant id must match {INVARIANT_ID.pattern}, got {self.id!r}",
        )
        non_empty(self.description, "description")
        require(len(self.requires) >= 1, "an invariant needs at least one requirement")


@dataclass(frozen=True, slots=True, kw_only=True)
class Policy:
    name: str
    requirements: tuple[Requirement, ...]
    require_complete_analysis: bool

    def __post_init__(self) -> None:
        check_types(self)
        non_empty(self.name, "name")
        sorted_unique([requirement.id for requirement in self.requirements], "requirements")


@dataclass(frozen=True, slots=True, kw_only=True)
class RequirementResult:
    requirement_id: str
    kind: str
    level: RequirementLevel
    status: RequirementStatus
    evidence_ids: tuple[EvidenceId, ...]
    message: str | None

    def __post_init__(self) -> None:
        check_types(self)
        non_empty(self.requirement_id, "requirement_id")
        non_empty(self.kind, "kind")
        for evidence_id in self.evidence_ids:
            validate_evidence_id(evidence_id)
        sorted_unique(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyResult:
    verdict: Verdict
    requirement_results: tuple[RequirementResult, ...]
    summary: str

    def __post_init__(self) -> None:
        check_types(self)
        sorted_unique(
            [result.requirement_id for result in self.requirement_results], "requirement_results"
        )
