"""Evidence, claims and limitations (SPEC §6.4, §13). The evidence engine is M6."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from rift.core.clock import check_timestamp
from rift.core.frozen import FrozenMap
from rift.core.ids import (
    EvidenceId,
    IdPrefix,
    validate_content_id,
    validate_evidence_id,
)
from rift.core.models._checks import check_types, non_empty, require, sorted_unique
from rift.core.models.graph import Confidence
from rift.core.models.source import SourceLocation


class EvidenceType(StrEnum):
    GIT_CHANGE = "git_change"
    STATIC_ANALYSIS = "static_analysis"
    GRAPH_EDGE = "graph_edge"
    IMPACT_ANALYSIS = "impact_analysis"
    TEST_DISCOVERY = "test_discovery"
    TEST_EXECUTION = "test_execution"
    INVARIANT_CHECK = "invariant_check"
    CONFIG = "config"
    ENVIRONMENT = "environment"


class EvidenceResult(StrEnum):
    PASS = "pass"  # noqa: S105  # a verdict value, not a password
    FAIL = "fail"
    ERROR = "error"
    INFO = "info"
    UNKNOWN = "unknown"


class Epistemic(StrEnum):
    """Facts, inferences and unknowns are kept explicitly separate (SPEC §13)."""

    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class ClaimKind(StrEnum):
    """Provisional claim vocabulary (ADR-0005); finalized with the proof schema in M6."""

    FILE_CHANGED = "file_changed"
    NODE_AFFECTED = "node_affected"
    TEST_SELECTED = "test_selected"
    TEST_EXECUTED = "test_executed"
    REQUIREMENT_EVALUATED = "requirement_evaluated"


class ClaimStatus(StrEnum):
    """Provisional (ADR-0005). Mirrors ``Epistemic`` so a claim never outranks its evidence."""

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    id: EvidenceId
    type: EvidenceType
    producer: str
    producer_version: str
    subject: str
    result: EvidenceResult
    source_location: SourceLocation | None
    collected_at: datetime
    confidence: Confidence
    epistemic: Epistemic
    limitations: tuple[str, ...]
    data: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self) -> None:
        check_types(self)
        validate_evidence_id(self.id)
        non_empty(self.producer, "producer")
        non_empty(self.producer_version, "producer_version")
        non_empty(self.subject, "subject")
        check_timestamp(self.collected_at, "collected_at")
        # Never present an inference as a fact (SPEC §2.2): facts are certain by definition.
        require(
            self.epistemic is not Epistemic.FACT or self.confidence is Confidence.CERTAIN,
            "fact evidence must have confidence=certain",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Claim:
    id: str
    statement_kind: ClaimKind
    subject: str
    evidence_ids: tuple[EvidenceId, ...]
    status: ClaimStatus

    def __post_init__(self) -> None:
        check_types(self)
        validate_content_id(self.id, IdPrefix.CLAIM)
        non_empty(self.subject, "subject")
        require(len(self.evidence_ids) >= 1, "every claim references at least one evidence ID")
        for evidence_id in self.evidence_ids:
            validate_evidence_id(evidence_id)
        sorted_unique(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True, kw_only=True)
class Limitation:
    """Something RIFT could not determine (analysis limitation, coverage gap, ...)."""

    kind: str
    message: str
    subject: str | None

    def __post_init__(self) -> None:
        check_types(self)
        non_empty(self.kind, "kind")
        non_empty(self.message, "message")
        if self.subject is not None:
            non_empty(self.subject, "subject")
