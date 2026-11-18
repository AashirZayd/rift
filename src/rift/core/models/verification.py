"""Verification plans and runs (SPEC §6.4, §12). Planning/execution are M5; these are values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rift.core.clock import check_timestamp
from rift.core.ids import IdPrefix, NodeId, validate_content_id, validate_node_id
from rift.core.models._checks import non_empty, non_negative, require


class PlanMode(StrEnum):
    SELECTIVE = "selective"
    FULL_SUITE = "full_suite"


class TestOutcome(StrEnum):
    __test__ = False  # not a pytest test class; dunder names are not enum members

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"
    XPASSED = "xpassed"
    NOT_RUN = "not_run"


def _test_id(node_id: NodeId, field: str) -> None:
    validate_node_id(node_id)
    require(node_id.startswith("test:"), f"{field} must be a test: node id, got {node_id!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedCheck:
    node_id: NodeId
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _test_id(self.node_id, "node_id")
        require(len(self.reasons) >= 1, "a planned check needs at least one reason")
        for reason in self.reasons:
            non_empty(reason, "reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExclusionRecord:
    """A discovered test that was not selected, and why (nothing is silently omitted)."""

    node_id: NodeId
    reason: str

    def __post_init__(self) -> None:
        _test_id(self.node_id, "node_id")
        non_empty(self.reason, "reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class VerificationPlan:
    mode: PlanMode
    selected: tuple[PlannedCheck, ...]
    excluded: tuple[ExclusionRecord, ...]
    full_suite_recommended: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        selected = [check.node_id for check in self.selected]
        excluded = [record.node_id for record in self.excluded]
        require(selected == sorted(set(selected)), "selected must be sorted by node_id, unique")
        require(excluded == sorted(set(excluded)), "excluded must be sorted by node_id, unique")
        require(not set(selected) & set(excluded), "a test cannot be both selected and excluded")


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolInfo:
    name: str
    version: str

    def __post_init__(self) -> None:
        non_empty(self.name, "name")
        non_empty(self.version, "version")


@dataclass(frozen=True, slots=True, kw_only=True)
class TestResult:
    __test__ = False  # not a pytest test class

    node_id: NodeId
    outcome: TestOutcome
    duration_ms: int
    message: str | None

    def __post_init__(self) -> None:
        _test_id(self.node_id, "node_id")
        non_negative(self.duration_ms, "duration_ms")


@dataclass(frozen=True, slots=True, kw_only=True)
class VerificationRun:
    plan_id: str
    command: tuple[str, ...]
    exit_code: int | None  # None: the process was killed (e.g. timeout) before exiting
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    tool: ToolInfo
    results: tuple[TestResult, ...]
    timed_out: bool

    def __post_init__(self) -> None:
        validate_content_id(self.plan_id, IdPrefix.PLAN)
        require(len(self.command) >= 1, "command must not be empty")
        check_timestamp(self.started_at, "started_at")
        check_timestamp(self.finished_at, "finished_at")
        require(self.finished_at >= self.started_at, "finished_at must not precede started_at")
        non_negative(self.duration_ms, "duration_ms")
        ids = [result.node_id for result in self.results]
        require(ids == sorted(set(ids)), "results must be sorted by node_id, unique")
