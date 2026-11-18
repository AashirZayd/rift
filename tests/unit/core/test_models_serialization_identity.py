"""Model invariants, strict decoding, and content-derived model IDs."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rift.core.errors import ErrorCode, ModelValidationError, SerializationError
from rift.core.frozen import FrozenMap
from rift.core.identity import (
    claim_id,
    evidence_id,
    plan_id,
    proof_id,
    with_claim_id,
    with_evidence_id,
    with_proof_id,
)
from rift.core.ids import EvidenceId, NodeId, SymbolId
from rift.core.models.change import (
    ChangedFile,
    ChangeSet,
    ChangeStatus,
    LineRange,
    merge_line_ranges,
)
from rift.core.models.evidence import (
    Claim,
    ClaimKind,
    ClaimStatus,
    Epistemic,
    Evidence,
    EvidenceResult,
    EvidenceType,
)
from rift.core.models.graph import Confidence, Edge, EdgeFlag, EdgeKind, EdgeOrigin, Node, NodeKind
from rift.core.models.impact import ImpactExplanation
from rift.core.models.policy import Invariant, RequirementLevel, Scope, Verdict
from rift.core.models.proof import (
    PROTOCOL_VERSION,
    ChangeProof,
    ProofEnvironment,
    ProofPolicy,
    ProofSubject,
    ProofVerification,
    url_has_credentials,
)
from rift.core.models.repo import Repository, RepositoryRevision
from rift.core.models.source import SourceLocation, Symbol, SymbolKind, Visibility
from rift.core.models.verification import (
    ExclusionRecord,
    PlanMode,
    PlannedCheck,
    ToolInfo,
    VerificationPlan,
    VerificationRun,
)
from rift.core.serialization import from_dict, to_dict

SHA = "a" * 40
EV1 = EvidenceId("ev_" + "1" * 24)
EV2 = EvidenceId("ev_" + "2" * 24)
T0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
TEST_A = NodeId("test:tests/test_a.py::test_one")


def revision(**kw: Any) -> RepositoryRevision:
    base: dict[str, Any] = {"commit": SHA, "ref": "main", "is_detached": False, "is_dirty": False}
    return RepositoryRevision(**{**base, **kw})


def make_evidence(**kw: Any) -> Evidence:
    base: dict[str, Any] = {
        "id": EV1,
        "type": EvidenceType.GIT_CHANGE,
        "producer": "rift.git",
        "producer_version": "0.0.0",
        "subject": "file:src/a.py",
        "result": EvidenceResult.INFO,
        "source_location": None,
        "collected_at": T0,
        "confidence": Confidence.CERTAIN,
        "epistemic": Epistemic.FACT,
        "limitations": (),
        "data": FrozenMap.of({"status": "modified"}),
    }
    return Evidence(**{**base, **kw})


def make_run(**kw: Any) -> VerificationRun:
    base: dict[str, Any] = {
        "plan_id": "pl_" + "0" * 24,
        "command": ("python", "-m", "pytest"),
        "exit_code": 0,
        "started_at": T0,
        "finished_at": T0 + timedelta(seconds=3),
        "duration_ms": 3000,
        "tool": ToolInfo(name="pytest", version="9.2.0"),
        "results": (),
        "timed_out": False,
    }
    return VerificationRun(**{**base, **kw})


def make_proof(**kw: Any) -> ChangeProof:
    base: dict[str, Any] = {
        "schema_version": PROTOCOL_VERSION,
        "proof_id": "rp_" + "0" * 24,
        "generated_at": T0,
        "subject": ProofSubject(
            remote_url=None, base=revision(), head=revision(commit=None), is_dirty=True
        ),
        "environment": ProofEnvironment(
            rift_version="0.0.0",
            python_version="3.11.9",
            platform="linux",
            git_version="2.30.2",
            tools=(),
            config_hash="0" * 64,
        ),
        "changes": (),
        "claims": (),
        "evidence": (make_evidence(),),
        "verification": ProofVerification(plan=None, runs=(make_run(),)),
        "limitations": (),
        "policy": ProofPolicy(policy_hash="f" * 64, requirement_results=()),
        "verdict": Verdict.INCONCLUSIVE,
    }
    return ChangeProof(**{**base, **kw})


# --------------------------------------------------------------------------- invariants


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: LineRange(start=0, end=1), "start"),
        (lambda: LineRange(start=5, end=4), "end"),
        (lambda: LineRange(start=True, end=True), "start"),
        (lambda: revision(commit="ABC"), "commit"),
        (lambda: revision(commit="a" * 39), "commit"),
        (lambda: revision(is_detached=True, ref="main"), "detached"),
        (lambda: Repository(root=Path("rel"), is_shallow=False, default_branch=None), "root"),
        (
            lambda: ChangedFile(
                path="a",
                old_path=None,
                status=ChangeStatus.RENAMED,
                changed_lines=(),
                is_binary=False,
            ),
            "old_path",
        ),
        (
            lambda: ChangedFile(
                path="a",
                old_path="b",
                status=ChangeStatus.MODIFIED,
                changed_lines=(),
                is_binary=False,
            ),
            "old_path",
        ),
        (
            lambda: ChangedFile(
                path="a",
                old_path=None,
                status=ChangeStatus.MODIFIED,
                changed_lines=(LineRange(start=5, end=6), LineRange(start=1, end=2)),
                is_binary=False,
            ),
            "sorted",
        ),
        (
            lambda: ChangedFile(
                path="a",
                old_path=None,
                status=ChangeStatus.MODIFIED,
                changed_lines=(LineRange(start=1, end=2),),
                is_binary=True,
            ),
            "binary",
        ),
        (
            lambda: ChangeSet(
                base=revision(commit=None), head=revision(), files=(), includes_uncommitted=False
            ),
            "base",
        ),
        (
            lambda: ChangeSet(
                base=revision(), head=revision(commit=None), files=(), includes_uncommitted=False
            ),
            "includes_uncommitted",
        ),
        (
            lambda: SourceLocation(path="a.py", start_line=3, start_col=5, end_line=3, end_col=4),
            "end_col",
        ),
        (
            lambda: Symbol(
                id=SymbolId("py:a:other"),
                kind=SymbolKind.FUNCTION,
                name="f",
                qualname="f",
                module="a",
                location=SourceLocation(
                    path="a.py", start_line=1, start_col=0, end_line=1, end_col=1
                ),
                visibility=Visibility.PUBLIC,
                decorators=(),
            ),
            "symbol id",
        ),
        (
            lambda: Node(id=NodeId("py:a:f"), kind=NodeKind.MODULE, label="a", location=None),
            "module",
        ),
        (
            lambda: Node(id=NodeId("file:a.py"), kind=NodeKind.TEST, label="a", location=None),
            "test",
        ),
        (
            lambda: Edge(
                source=NodeId("py:a"),
                target=NodeId("py:b"),
                kind=EdgeKind.IMPORTS,
                origin=EdgeOrigin.AST_STATIC,
                confidence=Confidence.CERTAIN,
                evidence_ids=(EV2, EV1),
                flags=frozenset(),
            ),
            "sorted",
        ),
        (lambda: ImpactExplanation(node=NodeId("py:a"), path=(NodeId("py:b"),)), "end at"),
        (lambda: make_evidence(epistemic=Epistemic.FACT, confidence=Confidence.LIKELY), "fact"),
        (
            lambda: Claim(
                id="cl_" + "0" * 24,
                statement_kind=ClaimKind.FILE_CHANGED,
                subject="file:a.py",
                evidence_ids=(),
                status=ClaimStatus.CONFIRMED,
            ),
            "at least one evidence",
        ),
        (
            lambda: Invariant(
                id="Bad_Id",
                description="d",
                scope=Scope(paths=(), symbols=()),
                level=RequirementLevel.MANDATORY,
                requires=(),
            ),
            "invariant id",
        ),
        (
            lambda: VerificationPlan(
                mode=PlanMode.SELECTIVE,
                selected=(PlannedCheck(node_id=TEST_A, reasons=("r",)),),
                excluded=(ExclusionRecord(node_id=TEST_A, reason="r"),),
                full_suite_recommended=False,
                reasons=(),
            ),
            "both selected and excluded",
        ),
        (lambda: PlannedCheck(node_id=NodeId("py:a"), reasons=("r",)), "test: node id"),
        (lambda: make_run(finished_at=T0 - timedelta(seconds=1)), "precede"),
        (lambda: make_run(command=()), "command"),
        (lambda: make_proof(schema_version="0.1"), "SemVer"),
        (lambda: make_proof(proof_id="ev_" + "0" * 24), "rp id"),
        (
            lambda: ProofSubject(
                remote_url="https://user:token@example.invalid/r.git",
                base=revision(),
                head=revision(),
                is_dirty=False,
            ),
            "credentials",
        ),
    ],
)
def test_model_invariants_are_enforced(factory: Any, message: str) -> None:
    with pytest.raises(ModelValidationError, match=message):
        factory()


def test_merge_line_ranges_sorts_and_merges_overlap_and_adjacency() -> None:
    ranges = [
        LineRange(start=10, end=12),
        LineRange(start=1, end=3),
        LineRange(start=4, end=4),
        LineRange(start=11, end=20),
        LineRange(start=30, end=30),
    ]
    assert merge_line_ranges(ranges) == (
        LineRange(start=1, end=4),
        LineRange(start=10, end=20),
        LineRange(start=30, end=30),
    )


def test_confidence_orders_by_strength_not_alphabet() -> None:
    assert Confidence.CERTAIN > Confidence.LIKELY > Confidence.POSSIBLE > Confidence.UNKNOWN
    assert sorted([Confidence.LIKELY, Confidence.UNKNOWN, Confidence.CERTAIN]) == [
        Confidence.UNKNOWN,
        Confidence.LIKELY,
        Confidence.CERTAIN,
    ]
    assert max(Confidence) is Confidence.CERTAIN


@pytest.mark.parametrize(
    ("url", "leaks"),
    [
        ("https://example.invalid/r.git", False),
        ("https://token@example.invalid/r.git", True),
        ("https://user:pw@example.invalid/r.git", True),
        ("ssh://git@example.invalid/r.git", False),
        ("ssh://git:secret@example.invalid/r.git", True),
        ("git@example.invalid:org/r.git", False),
    ],
)
def test_url_credential_detection(url: str, leaks: bool) -> None:
    assert url_has_credentials(url) is leaks


# --------------------------------------------------------------------------- strict decoding


def test_unknown_and_missing_keys_are_rejected() -> None:
    data = to_dict(revision())
    with pytest.raises(SerializationError, match="unknown keys"):
        from_dict(RepositoryRevision, {**data, "extra": 1})
    del data["ref"]
    with pytest.raises(SerializationError, match="missing key 'ref'"):
        from_dict(RepositoryRevision, data)


@pytest.mark.parametrize(
    ("model", "data", "message"),
    [
        (LineRange, {"start": True, "end": 2}, "integer"),
        (LineRange, {"start": "1", "end": 2}, "integer"),
        (LineRange, [1, 2], "expected an object"),
        (
            RepositoryRevision,
            {"commit": None, "ref": 3, "is_detached": False, "is_dirty": False},
            "string",
        ),
        (
            RepositoryRevision,
            {"commit": None, "ref": None, "is_detached": 0, "is_dirty": False},
            "boolean",
        ),
        (LineRange, {"start": 0, "end": 2}, "start"),
    ],
)
def test_wrong_types_raise_serialization_error(
    model: type[Any], data: object, message: str
) -> None:
    with pytest.raises(SerializationError, match=message) as exc_info:
        from_dict(model, data)
    assert exc_info.value.code is ErrorCode.SERIALIZATION


def test_invalid_enum_value_and_nested_errors_name_their_location() -> None:
    data = to_dict(
        ChangeSet(base=revision(), head=revision(), files=(), includes_uncommitted=False)
    )
    data["files"] = [
        {
            "path": "a",
            "old_path": None,
            "status": "exploded",
            "changed_lines": [],
            "is_binary": False,
        }
    ]
    with pytest.raises(SerializationError, match=r"ChangeSet\.files\[\]\.status"):
        from_dict(ChangeSet, data)


def test_floats_inside_evidence_data_are_rejected_on_decode() -> None:
    data = to_dict(make_evidence())
    data["data"] = {"ratio": 0.5}  # type: ignore[dict-item]  # deliberately invalid
    with pytest.raises(SerializationError):
        from_dict(Evidence, data)


def test_edge_flags_serialize_sorted_and_must_decode_sorted() -> None:
    edge = Edge(
        source=NodeId("py:a"),
        target=NodeId("py:b"),
        kind=EdgeKind.IMPORTS,
        origin=EdgeOrigin.AST_STATIC,
        confidence=Confidence.CERTAIN,
        evidence_ids=(),
        flags=frozenset(EdgeFlag),
    )
    data = to_dict(edge)
    assert data["flags"] == sorted(flag.value for flag in EdgeFlag)
    data["flags"] = list(reversed(data["flags"]))
    with pytest.raises(SerializationError, match="sorted"):
        from_dict(Edge, data)


def test_hostname_is_absent_unless_recorded() -> None:
    env = make_proof().environment
    assert "hostname" not in to_dict(env)
    with_host = replace(env, hostname="build-7")
    assert to_dict(with_host)["hostname"] == "build-7"
    assert from_dict(ProofEnvironment, to_dict(with_host)) == with_host


def test_to_dict_rejects_unknown_types() -> None:
    with pytest.raises(SerializationError, match="no codec"):
        to_dict(object())
    with pytest.raises(SerializationError, match="no codec"):
        from_dict(dict, {})


def test_serialized_output_is_json_native() -> None:
    text = json.dumps(to_dict(make_proof()), allow_nan=False)
    assert from_dict(ChangeProof, json.loads(text)) == make_proof()


# --------------------------------------------------------------------------- identity


def test_evidence_id_ignores_collected_at_but_not_content() -> None:
    a = make_evidence()
    assert evidence_id(a) == evidence_id(replace(a, collected_at=T0 + timedelta(days=9)))
    assert evidence_id(a) == evidence_id(replace(a, id=EV2))  # the id field itself is excluded
    assert evidence_id(a) != evidence_id(replace(a, subject="file:src/b.py"))
    assert with_evidence_id(a).id == evidence_id(a)


def test_claim_and_plan_ids() -> None:
    claim = Claim(
        id="cl_" + "0" * 24,
        statement_kind=ClaimKind.FILE_CHANGED,
        subject="file:a.py",
        evidence_ids=(EV1,),
        status=ClaimStatus.CONFIRMED,
    )
    assert with_claim_id(claim).id == claim_id(claim)
    assert claim_id(claim) != claim_id(replace(claim, status=ClaimStatus.INFERRED))
    plan = VerificationPlan(
        mode=PlanMode.SELECTIVE, selected=(), excluded=(), full_suite_recommended=True, reasons=()
    )
    assert plan_id(plan).startswith("pl_")
    assert plan_id(plan) != plan_id(replace(plan, full_suite_recommended=False))


def test_proof_id_ignores_every_volatile_field() -> None:
    proof = make_proof()
    noisy = replace(
        proof,
        generated_at=T0 + timedelta(hours=5),
        environment=replace(proof.environment, hostname="ci-runner-42"),
        verification=ProofVerification(
            plan=None,
            runs=(
                make_run(
                    started_at=T0 + timedelta(hours=1),
                    finished_at=T0 + timedelta(hours=2),
                    duration_ms=99,
                ),
            ),
        ),
        evidence=(replace(make_evidence(), collected_at=T0 + timedelta(minutes=3)),),
    )
    assert proof_id(proof) == proof_id(noisy)
    assert proof_id(proof) != proof_id(replace(proof, verdict=Verdict.FAIL))
    assert with_proof_id(proof).proof_id == proof_id(proof)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: make_proof(verdict="inconclusive"), "verdict"),
        (
            lambda: LineRange(start=1.0, end=2),  # type: ignore[arg-type]  # deliberately invalid
            "start",
        ),
        (lambda: revision(is_dirty=1), "is_dirty"),
        (lambda: make_evidence(limitations=["a"]), "limitations"),
        (lambda: make_evidence(data={"a": 1}), "data"),
        (lambda: make_run(results=[]), "results"),
        (lambda: make_run(exit_code=True), "exit_code"),
        (lambda: make_evidence(id=1), "id"),
        (
            lambda: ProofVerification(plan="none", runs=()),  # type: ignore[arg-type]  # deliberately invalid
            "plan",
        ),
        (
            lambda: Edge(
                source=NodeId("py:a"),
                target=NodeId("py:b"),
                kind="imports",  # type: ignore[arg-type]  # deliberately invalid
                origin=EdgeOrigin.AST_STATIC,
                confidence=Confidence.CERTAIN,
                evidence_ids=(),
                flags=frozenset(),
            ),
            "kind",
        ),
        (
            lambda: Edge(
                source=NodeId("py:a"),
                target=NodeId("py:b"),
                kind=EdgeKind.IMPORTS,
                origin=EdgeOrigin.AST_STATIC,
                confidence=Confidence.CERTAIN,
                evidence_ids=(),
                flags=frozenset({"dynamic"}),  # type: ignore[arg-type]  # deliberately invalid
            ),
            "flags",
        ),
    ],
)
def test_wrongly_typed_fields_are_rejected_at_construction(factory: Any, field: str) -> None:
    with pytest.raises(ModelValidationError, match=rf"\.{field}: "):
        factory()
