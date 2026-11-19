"""Explicit model ⇄ JSON-native dict conversion (SPEC §6: no reflection magic).

``to_dict(model)`` produces only JSON-native values (dict/list/str/int/bool/None); tuples become
lists in model order, frozensets become sorted lists, enums their values, timestamps RFC 3339.
``from_dict(Model, data)`` is strict: missing keys, unknown keys and wrong types raise
:class:`SerializationError` (ADR-0005). Every failure surfaces as ``SerializationError``.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar, cast

from rift.core.canonical import JsonValue
from rift.core.clock import format_timestamp, parse_timestamp
from rift.core.errors import ModelValidationError, SerializationError
from rift.core.frozen import FrozenMap, freeze, thaw
from rift.core.ids import EvidenceId, NodeId, SymbolId
from rift.core.models.analysis import AnalysisResult
from rift.core.models.change import ChangedFile, ChangeSet, ChangeStatus, LineRange
from rift.core.models.diagnostic import Diagnostic, DiagnosticSeverity
from rift.core.models.evidence import (
    Claim,
    ClaimKind,
    ClaimStatus,
    Epistemic,
    Evidence,
    EvidenceResult,
    EvidenceType,
    Limitation,
)
from rift.core.models.graph import (
    Confidence,
    Edge,
    EdgeFlag,
    EdgeKind,
    EdgeOrigin,
    Node,
    NodeKind,
    UnresolvedReason,
    UnresolvedReference,
)
from rift.core.models.impact import Completeness, ImpactClass, ImpactExplanation, ImpactResult
from rift.core.models.plugin import PluginKind, PluginMetadata
from rift.core.models.policy import (
    Invariant,
    Policy,
    PolicyResult,
    Requirement,
    RequirementLevel,
    RequirementResult,
    RequirementStatus,
    Scope,
    Verdict,
)
from rift.core.models.proof import (
    ChangeProof,
    ProofEnvironment,
    ProofPolicy,
    ProofSubject,
    ProofVerification,
)
from rift.core.models.repo import Repository, RepositoryRevision, Vcs
from rift.core.models.source import SourceLocation, Symbol, SymbolKind, Visibility
from rift.core.models.verification import (
    ExclusionRecord,
    PlanMode,
    PlannedCheck,
    TestOutcome,
    TestResult,
    ToolInfo,
    VerificationPlan,
    VerificationRun,
)

T = TypeVar("T")
E = TypeVar("E", bound=StrEnum)
JsonDict = dict[str, JsonValue]


class _Reader:
    """Typed, consuming access to one JSON object; ``done()`` rejects leftover keys."""

    def __init__(self, data: object, what: str) -> None:
        if not isinstance(data, dict):
            raise SerializationError(f"{what}: expected an object, got {type(data).__name__}")
        self._data: dict[str, object] = data
        self._what = what
        self._seen: set[str] = set()

    def _get(self, key: str) -> object:
        if key not in self._data:
            raise SerializationError(f"{self._what}: missing key {key!r}")
        self._seen.add(key)
        return self._data[key]

    def _fail(self, key: str, expected: str, value: object) -> SerializationError:
        return SerializationError(
            f"{self._what}.{key}: expected {expected}, got {type(value).__name__}"
        )

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            raise self._fail(key, "string", value)
        return value

    def opt_text(self, key: str) -> str | None:
        value = self._get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._fail(key, "string or null", value)
        return value

    def integer(self, key: str) -> int:
        value = self._get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._fail(key, "integer", value)
        return value

    def opt_integer(self, key: str) -> int | None:
        if self._data.get(key, 0) is None:
            self._get(key)
            return None
        return self.integer(key)

    def boolean(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise self._fail(key, "boolean", value)
        return value

    def enum(self, key: str, enum_type: type[E]) -> E:
        value = self.text(key)
        try:
            return enum_type(value)
        except ValueError as exc:
            raise SerializationError(
                f"{self._what}.{key}: {value!r} is not a valid {enum_type.__name__}"
            ) from exc

    def array(self, key: str) -> list[object]:
        value = self._get(key)
        if not isinstance(value, list):
            raise self._fail(key, "array", value)
        return value

    def str_tuple(self, key: str) -> tuple[str, ...]:
        items = self.array(key)
        for item in items:
            if not isinstance(item, str):
                raise self._fail(key, "array of strings", item)
        return tuple(cast("list[str]", items))

    def models(self, key: str, model: type[T]) -> tuple[T, ...]:
        return tuple(_decode(model, item, f"{self._what}.{key}[]") for item in self.array(key))

    def model(self, key: str, model: type[T]) -> T:
        return _decode(model, self._get(key), f"{self._what}.{key}")

    def opt_model(self, key: str, model: type[T]) -> T | None:
        value = self._get(key)
        return None if value is None else _decode(model, value, f"{self._what}.{key}")

    def has(self, key: str) -> bool:
        return key in self._data

    def raw(self, key: str) -> object:
        return self._get(key)

    def done(self) -> None:
        unknown = sorted(set(self._data) - self._seen)
        if unknown:
            raise SerializationError(f"{self._what}: unknown keys {unknown}")


# --------------------------------------------------------------------------- encoders


def _location(value: SourceLocation | None) -> JsonValue:
    return None if value is None else _encode_source_location(value)


def _encode_repository(m: Repository) -> JsonDict:
    return {
        "root": m.root.as_posix(),
        "vcs": m.vcs.value,
        "is_shallow": m.is_shallow,
        "default_branch": m.default_branch,
    }


def _encode_revision(m: RepositoryRevision) -> JsonDict:
    return {
        "commit": m.commit,
        "ref": m.ref,
        "is_detached": m.is_detached,
        "is_dirty": m.is_dirty,
    }


def _encode_line_range(m: LineRange) -> JsonDict:
    return {"start": m.start, "end": m.end}


def _encode_changed_file(m: ChangedFile) -> JsonDict:
    return {
        "path": m.path,
        "old_path": m.old_path,
        "status": m.status.value,
        "changed_lines": [_encode_line_range(r) for r in m.changed_lines],
        "is_binary": m.is_binary,
    }


def _encode_change_set(m: ChangeSet) -> JsonDict:
    return {
        "base": _encode_revision(m.base),
        "head": _encode_revision(m.head),
        "files": [_encode_changed_file(f) for f in m.files],
        "includes_uncommitted": m.includes_uncommitted,
    }


def _encode_source_location(m: SourceLocation) -> JsonDict:
    return {
        "path": m.path,
        "start_line": m.start_line,
        "start_col": m.start_col,
        "end_line": m.end_line,
        "end_col": m.end_col,
    }


def _encode_symbol(m: Symbol) -> JsonDict:
    return {
        "id": m.id,
        "kind": m.kind.value,
        "name": m.name,
        "qualname": m.qualname,
        "module": m.module,
        "location": _encode_source_location(m.location),
        "visibility": m.visibility.value,
        "decorators": list(m.decorators),
    }


def _encode_node(m: Node) -> JsonDict:
    return {
        "id": m.id,
        "kind": m.kind.value,
        "label": m.label,
        "location": _location(m.location),
        "attributes": thaw(m.attributes),
    }


def _encode_edge(m: Edge) -> JsonDict:
    return {
        "source": m.source,
        "target": m.target,
        "kind": m.kind.value,
        "origin": m.origin.value,
        "confidence": m.confidence.value,
        "evidence_ids": list(m.evidence_ids),
        "flags": [value for value in sorted(flag.value for flag in m.flags)],
    }


def _encode_unresolved(m: UnresolvedReference) -> JsonDict:
    return {
        "source": m.source,
        "raw": m.raw,
        "reason": m.reason.value,
        "location": _encode_source_location(m.location),
    }


def _encode_diagnostic(m: Diagnostic) -> JsonDict:
    return {
        "severity": m.severity.value,
        "code": m.code,
        "message": m.message,
        "subject": m.subject,
        "location": _location(m.location),
    }


def _encode_analysis(m: AnalysisResult) -> JsonDict:
    return {
        "file_path": m.file_path,
        "module": m.module,
        "content_sha256": m.content_sha256,
        "analyzer_name": m.analyzer_name,
        "analyzer_version": m.analyzer_version,
        "python_version": m.python_version,
        "symbols": [_encode_symbol(s) for s in m.symbols],
        "edges": [_encode_edge(e) for e in m.edges],
        "unresolved": [_encode_unresolved(u) for u in m.unresolved],
        "diagnostics": [_encode_diagnostic(d) for d in m.diagnostics],
        "limitations": [_encode_limitation(lim) for lim in m.limitations],
    }


def _encode_explanation(m: ImpactExplanation) -> JsonDict:
    return {"node": m.node, "path": list(m.path)}


def _encode_impact(m: ImpactResult) -> JsonDict:
    return {
        "changed_nodes": list(m.changed_nodes),
        "affected_nodes": list(m.affected_nodes),
        "public_interfaces": list(m.public_interfaces),
        "related_tests": list(m.related_tests),
        "unresolved": [_encode_unresolved(u) for u in m.unresolved],
        "classification": m.classification.value,
        "completeness": m.completeness.value,
        "limitations": [_encode_limitation(lim) for lim in m.limitations],
        "explanations": [_encode_explanation(e) for e in m.explanations],
    }


def _encode_planned_check(m: PlannedCheck) -> JsonDict:
    return {"node_id": m.node_id, "reasons": list(m.reasons)}


def _encode_exclusion(m: ExclusionRecord) -> JsonDict:
    return {"node_id": m.node_id, "reason": m.reason}


def _encode_plan(m: VerificationPlan) -> JsonDict:
    return {
        "mode": m.mode.value,
        "selected": [_encode_planned_check(c) for c in m.selected],
        "excluded": [_encode_exclusion(e) for e in m.excluded],
        "full_suite_recommended": m.full_suite_recommended,
        "reasons": list(m.reasons),
    }


def _encode_tool(m: ToolInfo) -> JsonDict:
    return {"name": m.name, "version": m.version}


def _encode_test_result(m: TestResult) -> JsonDict:
    return {
        "node_id": m.node_id,
        "outcome": m.outcome.value,
        "duration_ms": m.duration_ms,
        "message": m.message,
    }


def _encode_run(m: VerificationRun) -> JsonDict:
    return {
        "plan_id": m.plan_id,
        "command": list(m.command),
        "exit_code": m.exit_code,
        "started_at": format_timestamp(m.started_at),
        "finished_at": format_timestamp(m.finished_at),
        "duration_ms": m.duration_ms,
        "tool": _encode_tool(m.tool),
        "results": [_encode_test_result(r) for r in m.results],
        "timed_out": m.timed_out,
    }


def _encode_evidence(m: Evidence) -> JsonDict:
    return {
        "id": m.id,
        "type": m.type.value,
        "producer": m.producer,
        "producer_version": m.producer_version,
        "subject": m.subject,
        "result": m.result.value,
        "source_location": _location(m.source_location),
        "collected_at": format_timestamp(m.collected_at),
        "confidence": m.confidence.value,
        "epistemic": m.epistemic.value,
        "limitations": list(m.limitations),
        "data": thaw(m.data),
    }


def _encode_claim(m: Claim) -> JsonDict:
    return {
        "id": m.id,
        "statement_kind": m.statement_kind.value,
        "subject": m.subject,
        "evidence_ids": list(m.evidence_ids),
        "status": m.status.value,
    }


def _encode_limitation(m: Limitation) -> JsonDict:
    return {"kind": m.kind, "message": m.message, "subject": m.subject}


def _encode_scope(m: Scope) -> JsonDict:
    return {"paths": list(m.paths), "symbols": list(m.symbols)}


def _encode_requirement(m: Requirement) -> JsonDict:
    return {
        "id": m.id,
        "kind": m.kind,
        "level": m.level.value,
        "select": None if m.select is None else _encode_scope(m.select),
    }


def _encode_invariant(m: Invariant) -> JsonDict:
    return {
        "id": m.id,
        "description": m.description,
        "scope": _encode_scope(m.scope),
        "level": m.level.value,
        "requires": [_encode_requirement(r) for r in m.requires],
    }


def _encode_policy(m: Policy) -> JsonDict:
    return {
        "name": m.name,
        "requirements": [_encode_requirement(r) for r in m.requirements],
        "require_complete_analysis": m.require_complete_analysis,
    }


def _encode_requirement_result(m: RequirementResult) -> JsonDict:
    return {
        "requirement_id": m.requirement_id,
        "kind": m.kind,
        "level": m.level.value,
        "status": m.status.value,
        "evidence_ids": list(m.evidence_ids),
        "message": m.message,
    }


def _encode_policy_result(m: PolicyResult) -> JsonDict:
    return {
        "verdict": m.verdict.value,
        "requirement_results": [_encode_requirement_result(r) for r in m.requirement_results],
        "summary": m.summary,
    }


def _encode_subject(m: ProofSubject) -> JsonDict:
    return {
        "remote_url": m.remote_url,
        "base": _encode_revision(m.base),
        "head": _encode_revision(m.head),
        "is_dirty": m.is_dirty,
    }


def _encode_environment(m: ProofEnvironment) -> JsonDict:
    out: JsonDict = {
        "rift_version": m.rift_version,
        "python_version": m.python_version,
        "platform": m.platform,
        "git_version": m.git_version,
        "tools": [_encode_tool(t) for t in m.tools],
        "config_hash": m.config_hash,
    }
    if m.hostname is not None:  # opt-in only; absent rather than null when not recorded
        out["hostname"] = m.hostname
    return out


def _encode_proof_verification(m: ProofVerification) -> JsonDict:
    return {
        "plan": None if m.plan is None else _encode_plan(m.plan),
        "runs": [_encode_run(r) for r in m.runs],
    }


def _encode_proof_policy(m: ProofPolicy) -> JsonDict:
    return {
        "policy_hash": m.policy_hash,
        "requirement_results": [_encode_requirement_result(r) for r in m.requirement_results],
    }


def _encode_plugin(m: PluginMetadata) -> JsonDict:
    return {
        "name": m.name,
        "version": m.version,
        "api_version": m.api_version,
        "kind": m.kind.value,
        "capabilities": list(m.capabilities),
        "file_patterns": list(m.file_patterns),
        "requirements": list(m.requirements),
    }


def _encode_proof(m: ChangeProof) -> JsonDict:
    return {
        "schema_version": m.schema_version,
        "proof_id": m.proof_id,
        "generated_at": format_timestamp(m.generated_at),
        "subject": _encode_subject(m.subject),
        "environment": _encode_environment(m.environment),
        "changes": [_encode_changed_file(c) for c in m.changes],
        "claims": [_encode_claim(c) for c in m.claims],
        "evidence": [_encode_evidence(e) for e in m.evidence],
        "verification": _encode_proof_verification(m.verification),
        "limitations": [_encode_limitation(lim) for lim in m.limitations],
        "policy": _encode_proof_policy(m.policy),
        "verdict": m.verdict.value,
    }


# --------------------------------------------------------------------------- decoders


def _frozen_map(r: _Reader, key: str) -> FrozenMap:
    raw = r.raw(key)
    if not isinstance(raw, dict):
        raise SerializationError(f"{key}: expected an object")
    frozen = freeze(cast("JsonValue", raw))
    if not isinstance(frozen, FrozenMap):  # pragma: no cover - freeze(dict) is a FrozenMap
        raise SerializationError(f"{key}: expected an object")
    return frozen


def _node_ids(r: _Reader, key: str) -> tuple[NodeId, ...]:
    return tuple(NodeId(v) for v in r.str_tuple(key))


def _evidence_ids(r: _Reader, key: str) -> tuple[EvidenceId, ...]:
    return tuple(EvidenceId(v) for v in r.str_tuple(key))


def _decode_repository(r: _Reader) -> Repository:
    return Repository(
        root=Path(r.text("root")),
        vcs=r.enum("vcs", Vcs),
        is_shallow=r.boolean("is_shallow"),
        default_branch=r.opt_text("default_branch"),
    )


def _decode_revision(r: _Reader) -> RepositoryRevision:
    return RepositoryRevision(
        commit=r.opt_text("commit"),
        ref=r.opt_text("ref"),
        is_detached=r.boolean("is_detached"),
        is_dirty=r.boolean("is_dirty"),
    )


def _decode_line_range(r: _Reader) -> LineRange:
    return LineRange(start=r.integer("start"), end=r.integer("end"))


def _decode_changed_file(r: _Reader) -> ChangedFile:
    return ChangedFile(
        path=r.text("path"),
        old_path=r.opt_text("old_path"),
        status=r.enum("status", ChangeStatus),
        changed_lines=r.models("changed_lines", LineRange),
        is_binary=r.boolean("is_binary"),
    )


def _decode_change_set(r: _Reader) -> ChangeSet:
    return ChangeSet(
        base=r.model("base", RepositoryRevision),
        head=r.model("head", RepositoryRevision),
        files=r.models("files", ChangedFile),
        includes_uncommitted=r.boolean("includes_uncommitted"),
    )


def _decode_source_location(r: _Reader) -> SourceLocation:
    return SourceLocation(
        path=r.text("path"),
        start_line=r.integer("start_line"),
        start_col=r.integer("start_col"),
        end_line=r.integer("end_line"),
        end_col=r.integer("end_col"),
    )


def _decode_symbol(r: _Reader) -> Symbol:
    return Symbol(
        id=SymbolId(r.text("id")),
        kind=r.enum("kind", SymbolKind),
        name=r.text("name"),
        qualname=r.text("qualname"),
        module=r.text("module"),
        location=r.model("location", SourceLocation),
        visibility=r.enum("visibility", Visibility),
        decorators=r.str_tuple("decorators"),
    )


def _decode_node(r: _Reader) -> Node:
    return Node(
        id=NodeId(r.text("id")),
        kind=r.enum("kind", NodeKind),
        label=r.text("label"),
        location=r.opt_model("location", SourceLocation),
        attributes=_frozen_map(r, "attributes"),
    )


def _decode_edge(r: _Reader) -> Edge:
    flags = r.str_tuple("flags")
    if list(flags) != sorted(set(flags)):
        raise SerializationError("edge.flags must be sorted and unique")
    try:
        parsed_flags = frozenset(EdgeFlag(flag) for flag in flags)
    except ValueError as exc:
        raise SerializationError(f"edge.flags: {exc}") from exc
    return Edge(
        source=NodeId(r.text("source")),
        target=NodeId(r.text("target")),
        kind=r.enum("kind", EdgeKind),
        origin=r.enum("origin", EdgeOrigin),
        confidence=r.enum("confidence", Confidence),
        evidence_ids=_evidence_ids(r, "evidence_ids"),
        flags=parsed_flags,
    )


def _decode_unresolved(r: _Reader) -> UnresolvedReference:
    return UnresolvedReference(
        source=NodeId(r.text("source")),
        raw=r.text("raw"),
        reason=r.enum("reason", UnresolvedReason),
        location=r.model("location", SourceLocation),
    )


def _decode_diagnostic(r: _Reader) -> Diagnostic:
    return Diagnostic(
        severity=r.enum("severity", DiagnosticSeverity),
        code=r.text("code"),
        message=r.text("message"),
        subject=r.opt_text("subject"),
        location=r.opt_model("location", SourceLocation),
    )


def _decode_analysis(r: _Reader) -> AnalysisResult:
    return AnalysisResult(
        file_path=r.text("file_path"),
        module=r.opt_text("module"),
        content_sha256=r.text("content_sha256"),
        analyzer_name=r.text("analyzer_name"),
        analyzer_version=r.text("analyzer_version"),
        python_version=r.text("python_version"),
        symbols=r.models("symbols", Symbol),
        edges=r.models("edges", Edge),
        unresolved=r.models("unresolved", UnresolvedReference),
        diagnostics=r.models("diagnostics", Diagnostic),
        limitations=r.models("limitations", Limitation),
    )


def _decode_explanation(r: _Reader) -> ImpactExplanation:
    return ImpactExplanation(node=NodeId(r.text("node")), path=_node_ids(r, "path"))


def _decode_impact(r: _Reader) -> ImpactResult:
    return ImpactResult(
        changed_nodes=_node_ids(r, "changed_nodes"),
        affected_nodes=_node_ids(r, "affected_nodes"),
        public_interfaces=_node_ids(r, "public_interfaces"),
        related_tests=_node_ids(r, "related_tests"),
        unresolved=r.models("unresolved", UnresolvedReference),
        classification=r.enum("classification", ImpactClass),
        completeness=r.enum("completeness", Completeness),
        limitations=r.models("limitations", Limitation),
        explanations=r.models("explanations", ImpactExplanation),
    )


def _decode_planned_check(r: _Reader) -> PlannedCheck:
    return PlannedCheck(node_id=NodeId(r.text("node_id")), reasons=r.str_tuple("reasons"))


def _decode_exclusion(r: _Reader) -> ExclusionRecord:
    return ExclusionRecord(node_id=NodeId(r.text("node_id")), reason=r.text("reason"))


def _decode_plan(r: _Reader) -> VerificationPlan:
    return VerificationPlan(
        mode=r.enum("mode", PlanMode),
        selected=r.models("selected", PlannedCheck),
        excluded=r.models("excluded", ExclusionRecord),
        full_suite_recommended=r.boolean("full_suite_recommended"),
        reasons=r.str_tuple("reasons"),
    )


def _decode_tool(r: _Reader) -> ToolInfo:
    return ToolInfo(name=r.text("name"), version=r.text("version"))


def _decode_test_result(r: _Reader) -> TestResult:
    return TestResult(
        node_id=NodeId(r.text("node_id")),
        outcome=r.enum("outcome", TestOutcome),
        duration_ms=r.integer("duration_ms"),
        message=r.opt_text("message"),
    )


def _decode_run(r: _Reader) -> VerificationRun:
    return VerificationRun(
        plan_id=r.text("plan_id"),
        command=r.str_tuple("command"),
        exit_code=r.opt_integer("exit_code"),
        started_at=parse_timestamp(r.text("started_at")),
        finished_at=parse_timestamp(r.text("finished_at")),
        duration_ms=r.integer("duration_ms"),
        tool=r.model("tool", ToolInfo),
        results=r.models("results", TestResult),
        timed_out=r.boolean("timed_out"),
    )


def _decode_evidence(r: _Reader) -> Evidence:
    return Evidence(
        id=EvidenceId(r.text("id")),
        type=r.enum("type", EvidenceType),
        producer=r.text("producer"),
        producer_version=r.text("producer_version"),
        subject=r.text("subject"),
        result=r.enum("result", EvidenceResult),
        source_location=r.opt_model("source_location", SourceLocation),
        collected_at=parse_timestamp(r.text("collected_at")),
        confidence=r.enum("confidence", Confidence),
        epistemic=r.enum("epistemic", Epistemic),
        limitations=r.str_tuple("limitations"),
        data=_frozen_map(r, "data"),
    )


def _decode_claim(r: _Reader) -> Claim:
    return Claim(
        id=r.text("id"),
        statement_kind=r.enum("statement_kind", ClaimKind),
        subject=r.text("subject"),
        evidence_ids=_evidence_ids(r, "evidence_ids"),
        status=r.enum("status", ClaimStatus),
    )


def _decode_limitation(r: _Reader) -> Limitation:
    return Limitation(kind=r.text("kind"), message=r.text("message"), subject=r.opt_text("subject"))


def _decode_scope(r: _Reader) -> Scope:
    return Scope(paths=r.str_tuple("paths"), symbols=r.str_tuple("symbols"))


def _decode_requirement(r: _Reader) -> Requirement:
    return Requirement(
        id=r.text("id"),
        kind=r.text("kind"),
        level=r.enum("level", RequirementLevel),
        select=r.opt_model("select", Scope),
    )


def _decode_invariant(r: _Reader) -> Invariant:
    return Invariant(
        id=r.text("id"),
        description=r.text("description"),
        scope=r.model("scope", Scope),
        level=r.enum("level", RequirementLevel),
        requires=r.models("requires", Requirement),
    )


def _decode_policy(r: _Reader) -> Policy:
    return Policy(
        name=r.text("name"),
        requirements=r.models("requirements", Requirement),
        require_complete_analysis=r.boolean("require_complete_analysis"),
    )


def _decode_requirement_result(r: _Reader) -> RequirementResult:
    return RequirementResult(
        requirement_id=r.text("requirement_id"),
        kind=r.text("kind"),
        level=r.enum("level", RequirementLevel),
        status=r.enum("status", RequirementStatus),
        evidence_ids=_evidence_ids(r, "evidence_ids"),
        message=r.opt_text("message"),
    )


def _decode_policy_result(r: _Reader) -> PolicyResult:
    return PolicyResult(
        verdict=r.enum("verdict", Verdict),
        requirement_results=r.models("requirement_results", RequirementResult),
        summary=r.text("summary"),
    )


def _decode_subject(r: _Reader) -> ProofSubject:
    return ProofSubject(
        remote_url=r.opt_text("remote_url"),
        base=r.model("base", RepositoryRevision),
        head=r.model("head", RepositoryRevision),
        is_dirty=r.boolean("is_dirty"),
    )


def _decode_environment(r: _Reader) -> ProofEnvironment:
    hostname = r.text("hostname") if r.has("hostname") else None  # opt-in field (SPEC §7.4)
    return ProofEnvironment(
        rift_version=r.text("rift_version"),
        python_version=r.text("python_version"),
        platform=r.text("platform"),
        git_version=r.text("git_version"),
        tools=r.models("tools", ToolInfo),
        config_hash=r.text("config_hash"),
        hostname=hostname,
    )


def _decode_proof_verification(r: _Reader) -> ProofVerification:
    return ProofVerification(
        plan=r.opt_model("plan", VerificationPlan), runs=r.models("runs", VerificationRun)
    )


def _decode_proof_policy(r: _Reader) -> ProofPolicy:
    return ProofPolicy(
        policy_hash=r.text("policy_hash"),
        requirement_results=r.models("requirement_results", RequirementResult),
    )


def _decode_plugin(r: _Reader) -> PluginMetadata:
    return PluginMetadata(
        name=r.text("name"),
        version=r.text("version"),
        api_version=r.integer("api_version"),
        kind=r.enum("kind", PluginKind),
        capabilities=r.str_tuple("capabilities"),
        file_patterns=r.str_tuple("file_patterns"),
        requirements=r.str_tuple("requirements"),
    )


def _decode_proof(r: _Reader) -> ChangeProof:
    return ChangeProof(
        schema_version=r.text("schema_version"),
        proof_id=r.text("proof_id"),
        generated_at=parse_timestamp(r.text("generated_at")),
        subject=r.model("subject", ProofSubject),
        environment=r.model("environment", ProofEnvironment),
        changes=r.models("changes", ChangedFile),
        claims=r.models("claims", Claim),
        evidence=r.models("evidence", Evidence),
        verification=r.model("verification", ProofVerification),
        limitations=r.models("limitations", Limitation),
        policy=r.model("policy", ProofPolicy),
        verdict=r.enum("verdict", Verdict),
    )


# --------------------------------------------------------------------------- registry

_CODECS: Mapping[type[Any], tuple[Callable[[Any], JsonDict], Callable[[_Reader], Any]]] = (
    MappingProxyType(
        {
            Repository: (_encode_repository, _decode_repository),
            RepositoryRevision: (_encode_revision, _decode_revision),
            LineRange: (_encode_line_range, _decode_line_range),
            ChangedFile: (_encode_changed_file, _decode_changed_file),
            ChangeSet: (_encode_change_set, _decode_change_set),
            SourceLocation: (_encode_source_location, _decode_source_location),
            Symbol: (_encode_symbol, _decode_symbol),
            Node: (_encode_node, _decode_node),
            Edge: (_encode_edge, _decode_edge),
            UnresolvedReference: (_encode_unresolved, _decode_unresolved),
            Diagnostic: (_encode_diagnostic, _decode_diagnostic),
            AnalysisResult: (_encode_analysis, _decode_analysis),
            ImpactExplanation: (_encode_explanation, _decode_explanation),
            ImpactResult: (_encode_impact, _decode_impact),
            PlannedCheck: (_encode_planned_check, _decode_planned_check),
            ExclusionRecord: (_encode_exclusion, _decode_exclusion),
            VerificationPlan: (_encode_plan, _decode_plan),
            ToolInfo: (_encode_tool, _decode_tool),
            TestResult: (_encode_test_result, _decode_test_result),
            VerificationRun: (_encode_run, _decode_run),
            Evidence: (_encode_evidence, _decode_evidence),
            Claim: (_encode_claim, _decode_claim),
            Limitation: (_encode_limitation, _decode_limitation),
            Scope: (_encode_scope, _decode_scope),
            Requirement: (_encode_requirement, _decode_requirement),
            Invariant: (_encode_invariant, _decode_invariant),
            Policy: (_encode_policy, _decode_policy),
            RequirementResult: (_encode_requirement_result, _decode_requirement_result),
            PolicyResult: (_encode_policy_result, _decode_policy_result),
            ProofSubject: (_encode_subject, _decode_subject),
            ProofEnvironment: (_encode_environment, _decode_environment),
            ProofVerification: (_encode_proof_verification, _decode_proof_verification),
            ProofPolicy: (_encode_proof_policy, _decode_proof_policy),
            PluginMetadata: (_encode_plugin, _decode_plugin),
            ChangeProof: (_encode_proof, _decode_proof),
        }
    )
)

SERIALIZABLE_MODELS: tuple[type[Any], ...] = tuple(_CODECS)


def to_dict(model: object) -> JsonDict:
    """Encode a model into JSON-native values."""
    codec = _CODECS.get(type(model))
    if codec is None:
        raise SerializationError(f"no codec for {type(model).__name__}")
    return codec[0](model)


def _decode(model_type: type[T], data: object, what: str) -> T:
    codec = _CODECS.get(model_type)
    if codec is None:
        raise SerializationError(f"no codec for {model_type.__name__}")
    reader = _Reader(data, what)
    try:
        value = codec[1](reader)
    except ModelValidationError as exc:
        raise SerializationError(f"{what}: {exc.message}") from exc
    except (RecursionError, ValueError, TypeError) as exc:
        # Hostile documents (deep nesting, malformed URLs, oversized ints) never escape raw.
        if isinstance(exc, SerializationError):
            raise
        raise SerializationError(f"{what}: invalid document ({type(exc).__name__})") from exc
    reader.done()
    return cast("T", value)


def from_dict(model_type: type[T], data: object) -> T:
    """Decode JSON-native ``data`` into ``model_type`` (strict; see module docstring)."""
    return _decode(model_type, data, model_type.__name__)
