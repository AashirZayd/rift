"""Hypothesis strategies producing valid instances of every serializable model."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hypothesis import strategies as st

from rift.core.frozen import FrozenMap, FrozenValue
from rift.core.ids import (
    EvidenceId,
    NodeId,
    SymbolId,
    file_node_id,
    module_node_id,
    node_id_for_test,
    symbol_node_id,
)
from rift.core.models.analysis import (
    AnalysisResult,
    edge_sort_key,
    limitation_sort_key,
    sort_unique,
    symbol_sort_key,
    unresolved_sort_key,
)
from rift.core.models.analysis import diagnostic_sort_key as diagnostic_key
from rift.core.models.change import (
    ChangedFile,
    ChangeSet,
    ChangeStatus,
    LineRange,
    merge_line_ranges,
)
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
from rift.core.models.repo import Repository, RepositoryRevision
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
from rift.core.paths import is_repo_path

HEX = "0123456789abcdef"
SMALL = 4  # keep generated collections small so composite models stay fast

text = st.text(max_size=12)
nonempty_text = st.text(min_size=1, max_size=12)
identifier = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,6}", fullmatch=True)


def hex_string(length: int) -> st.SearchStrategy[str]:
    return st.text(HEX, min_size=length, max_size=length)


commit = st.one_of(hex_string(40), hex_string(64))
segment = st.text(
    st.characters(codec="utf-8", exclude_characters="/\x00"),
    min_size=1,
    max_size=6,
).filter(lambda s: s not in {".", ".."})
# A top-level segment such as ``A:`` reads as a Windows drive and is rejected by design
# (ADR-0005), so only generate paths the policy accepts.
repo_path = st.lists(segment, min_size=1, max_size=3).map("/".join).filter(is_repo_path)
module_name = st.lists(identifier, min_size=1, max_size=3).map(".".join)
qualname = st.lists(
    st.one_of(identifier, st.sampled_from(["<locals>", "<lambda>"])), min_size=1, max_size=3
).map(".".join)

file_id = repo_path.map(file_node_id)
module_id = module_name.map(module_node_id)
symbol_id = st.builds(symbol_node_id, module_name, qualname)
# pytest node IDs split path and name at the first "::", so a path containing ":" can be ambiguous
# (":" + "::A" == ":::A"). Real test files never contain ":"; such paths are rejected (ADR-0005).
_test_path = st.lists(segment.filter(lambda s: ":" not in s), min_size=1, max_size=3).map("/".join)
test_id = st.builds(lambda p, n: node_id_for_test(f"{p}::{n}"), _test_path, identifier)
node_id: st.SearchStrategy[NodeId] = st.one_of(file_id, module_id, symbol_id, test_id)


def content_id(prefix: str) -> st.SearchStrategy[str]:
    return hex_string(24).map(lambda h: f"{prefix}_{h}")


evidence_id = content_id("ev").map(EvidenceId)
evidence_ids = st.lists(evidence_id, max_size=SMALL, unique=True).map(lambda v: tuple(sorted(v)))
timestamp = st.datetimes(
    min_value=datetime(1970, 1, 1),  # noqa: DTZ001  # hypothesis bounds must be naive
    max_value=datetime(9999, 12, 30),  # noqa: DTZ001
    timezones=st.just(UTC),
).map(lambda d: d.replace(microsecond=0))

frozen_scalar = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=8))
frozen_value: st.SearchStrategy[FrozenValue] = st.recursive(
    frozen_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=3).map(tuple),
        st.dictionaries(st.text(max_size=5), children, max_size=3).map(FrozenMap.of),
    ),
    max_leaves=8,
)
frozen_map = st.dictionaries(st.text(max_size=5), frozen_value, max_size=SMALL).map(FrozenMap.of)

# --------------------------------------------------------------------------- repo / change

repository = st.builds(
    Repository,
    root=identifier.map(lambda name: Path(tempfile.gettempdir()).resolve() / name),
    is_shallow=st.booleans(),
    default_branch=st.none() | nonempty_text,
)


@st.composite
def revision(draw: Any, *, commit_required: bool = False) -> RepositoryRevision:
    is_detached = draw(st.booleans())
    return RepositoryRevision(
        commit=draw(commit) if commit_required else draw(st.none() | commit),
        ref=None if is_detached else draw(st.none() | nonempty_text),
        is_detached=is_detached,
        is_dirty=draw(st.booleans()),
    )


line_range = st.integers(1, 10**6).flatmap(
    lambda s: st.integers(s, s + 500).map(lambda e: LineRange(start=s, end=e))
)


@st.composite
def changed_file(draw: Any) -> ChangedFile:
    status = draw(st.sampled_from(ChangeStatus))
    is_binary = draw(st.booleans())
    needs_old = status in {ChangeStatus.RENAMED, ChangeStatus.COPIED}
    return ChangedFile(
        path=draw(repo_path),
        old_path=draw(repo_path) if needs_old else None,
        status=status,
        changed_lines=()
        if is_binary
        else merge_line_ranges(draw(st.lists(line_range, max_size=SMALL))),
        is_binary=is_binary,
    )


def sorted_files(files: list[ChangedFile]) -> tuple[ChangedFile, ...]:
    return tuple(sorted(files, key=lambda f: f.path))


changed_files = st.lists(changed_file(), max_size=SMALL, unique_by=lambda f: f.path).map(
    sorted_files
)


@st.composite
def change_set(draw: Any) -> ChangeSet:
    head = draw(revision())
    return ChangeSet(
        base=draw(revision(commit_required=True)),
        head=head,
        files=draw(changed_files),
        includes_uncommitted=head.commit is None,
    )


# --------------------------------------------------------------------------- source / graph


@st.composite
def source_location(draw: Any, path: str | None = None) -> SourceLocation:
    start_line = draw(st.integers(1, 10**5))
    end_line = draw(st.integers(start_line, start_line + 50))
    start_col = draw(st.integers(0, 200))
    end_col = draw(st.integers(start_col if end_line == start_line else 0, 300))
    return SourceLocation(
        path=draw(repo_path) if path is None else path,
        start_line=start_line,
        start_col=start_col,
        end_line=end_line,
        end_col=end_col,
    )


@st.composite
def symbol(draw: Any) -> Symbol:
    kind = draw(st.sampled_from(SymbolKind))
    module = draw(module_name)
    qual = draw(qualname)
    sid = module_node_id(module) if kind is SymbolKind.MODULE else symbol_node_id(module, qual)
    return Symbol(
        id=SymbolId(sid),
        kind=kind,
        name=draw(nonempty_text),
        qualname=qual,
        module=module,
        location=draw(source_location()),
        visibility=draw(st.sampled_from(Visibility)),
        decorators=tuple(draw(st.lists(nonempty_text, max_size=SMALL))),
    )


_ID_FOR_KIND: dict[NodeKind, st.SearchStrategy[NodeId]] = {
    NodeKind.FILE: file_id,
    NodeKind.MODULE: module_id,
    NodeKind.CLASS: symbol_id,
    NodeKind.FUNCTION: symbol_id,
    NodeKind.METHOD: symbol_id,
    NodeKind.CONSTANT: symbol_id,
    NodeKind.TEST: test_id,
    NodeKind.API_CONTRACT: node_id,
}


@st.composite
def node(draw: Any) -> Node:
    kind = draw(st.sampled_from(NodeKind))
    return Node(
        id=draw(_ID_FOR_KIND[kind]),
        kind=kind,
        label=draw(nonempty_text),
        location=draw(st.none() | source_location()),
        attributes=draw(frozen_map),
    )


edge = st.builds(
    Edge,
    source=node_id,
    target=node_id,
    kind=st.sampled_from(EdgeKind),
    origin=st.sampled_from(EdgeOrigin),
    confidence=st.sampled_from(Confidence),
    evidence_ids=evidence_ids,
    flags=st.frozensets(st.sampled_from(EdgeFlag)),
)

unresolved = st.builds(
    UnresolvedReference,
    source=node_id,
    raw=nonempty_text,
    reason=st.sampled_from(UnresolvedReason),
    location=source_location(),
)

diagnostic_code = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)


@st.composite
def diagnostic(draw: Any, path: str | None = None) -> Diagnostic:
    return Diagnostic(
        severity=draw(st.sampled_from(DiagnosticSeverity)),
        code=draw(diagnostic_code),
        message=draw(nonempty_text),
        subject=draw(st.none() | nonempty_text),
        location=draw(st.none() | source_location(path)),
    )


@st.composite
def analysis_result(draw: Any) -> AnalysisResult:
    """Internally consistent: symbols live in the file's module, edge and unresolved sources are
    nodes this file defines, and every collection is sorted by the model's key."""
    path = draw(repo_path)
    module = draw(st.none() | module_name)
    own: list[NodeId] = [file_node_id(path)]
    symbols: list[Symbol] = []
    if module is not None:
        own.append(module_node_id(module))
        for qual in draw(st.lists(qualname, max_size=SMALL, unique=True)):
            kind = draw(st.sampled_from([k for k in SymbolKind if k is not SymbolKind.MODULE]))
            symbols.append(
                Symbol(
                    id=SymbolId(symbol_node_id(module, qual)),
                    kind=kind,
                    name=qual.rsplit(".", 1)[-1],
                    qualname=qual,
                    module=module,
                    location=draw(source_location(path)),
                    visibility=draw(st.sampled_from(Visibility)),
                    decorators=tuple(draw(st.lists(nonempty_text, max_size=2))),
                )
            )
        own += [NodeId(sym.id) for sym in symbols]
    source = st.sampled_from(own)
    edges = draw(
        st.lists(
            st.builds(
                Edge,
                source=source,
                target=node_id,
                kind=st.sampled_from(EdgeKind),
                origin=st.sampled_from(EdgeOrigin),
                confidence=st.sampled_from(Confidence),
                evidence_ids=st.just(()),
                flags=st.frozensets(st.sampled_from(EdgeFlag)),
            ),
            max_size=SMALL,
        )
    )
    refs = draw(
        st.lists(
            st.builds(
                UnresolvedReference,
                source=source,
                raw=nonempty_text,
                reason=st.sampled_from(UnresolvedReason),
                location=source_location(path),
            ),
            max_size=SMALL,
        )
    )
    return AnalysisResult(
        file_path=path,
        module=module,
        content_sha256=draw(hex_string(64)),
        analyzer_name=draw(nonempty_text),
        analyzer_version=draw(nonempty_text),
        python_version=draw(st.from_regex(r"[0-9]\.[0-9]{1,2}", fullmatch=True)),
        symbols=sort_unique(symbols, symbol_sort_key),
        edges=_unique_by_key(edges, edge_sort_key),
        unresolved=_unique_by_key(refs, unresolved_sort_key),
        diagnostics=_unique_by_key(
            draw(st.lists(diagnostic(path), max_size=SMALL)), diagnostic_key
        ),
        limitations=_unique_by_key(draw(st.lists(limitation, max_size=SMALL)), limitation_sort_key),
    )


def _unique_by_key(values: list[Any], key: Any) -> tuple[Any, ...]:
    by_key = {key(value): value for value in values}
    return tuple(by_key[k] for k in sorted(by_key))


# --------------------------------------------------------------------------- impact


@st.composite
def explanation(draw: Any) -> ImpactExplanation:
    target = draw(node_id)
    prefix = draw(st.lists(node_id, max_size=3))
    return ImpactExplanation(node=target, path=(*prefix, target))


def sorted_node_ids(max_size: int = SMALL) -> st.SearchStrategy[tuple[NodeId, ...]]:
    return st.lists(node_id, max_size=max_size, unique=True).map(lambda v: tuple(sorted(v)))


@st.composite
def impact_result(draw: Any) -> ImpactResult:
    affected = draw(sorted_node_ids())
    related = draw(st.lists(test_id, max_size=SMALL, unique=True).map(lambda v: tuple(sorted(v))))
    extra = draw(sorted_node_ids())
    explained = sorted({*affected, *related, *extra})
    explanations = []
    for target in explained:
        prefix = draw(st.lists(node_id, max_size=3))
        explanations.append(ImpactExplanation(node=target, path=(*prefix, target)))
    completeness = draw(st.sampled_from(Completeness))
    limitations = (
        ()
        if completeness is Completeness.COMPLETE
        else _unique_by_key(
            draw(st.lists(limitation, min_size=1, max_size=SMALL)), limitation_sort_key
        )
    )
    return ImpactResult(
        changed_nodes=draw(sorted_node_ids()),
        affected_nodes=affected,
        public_interfaces=draw(sorted_node_ids()),
        related_tests=related,
        unresolved=_unique_by_key(draw(st.lists(unresolved, max_size=2)), unresolved_sort_key),
        classification=draw(st.sampled_from(ImpactClass)),
        completeness=completeness,
        limitations=limitations,
        explanations=tuple(explanations),
    )


# --------------------------------------------------------------------------- verification

reasons = st.lists(nonempty_text, min_size=1, max_size=3).map(tuple)
planned_check = st.builds(PlannedCheck, node_id=test_id, reasons=reasons)
exclusion = st.builds(ExclusionRecord, node_id=test_id, reason=nonempty_text)


@st.composite
def verification_plan(draw: Any) -> VerificationPlan:
    ids = sorted(draw(st.lists(test_id, max_size=6, unique=True)))
    split = draw(st.integers(0, len(ids)))
    return VerificationPlan(
        mode=draw(st.sampled_from(PlanMode)),
        selected=tuple(PlannedCheck(node_id=i, reasons=draw(reasons)) for i in ids[:split]),
        excluded=tuple(ExclusionRecord(node_id=i, reason=draw(nonempty_text)) for i in ids[split:]),
        full_suite_recommended=draw(st.booleans()),
        reasons=tuple(draw(st.lists(nonempty_text, max_size=3))),
    )


tool = st.builds(ToolInfo, name=nonempty_text, version=nonempty_text)
test_result = st.builds(
    TestResult,
    node_id=test_id,
    outcome=st.sampled_from(TestOutcome),
    duration_ms=st.integers(0, 10**9),
    message=st.none() | text,
)


@st.composite
def verification_run(draw: Any) -> VerificationRun:
    started = draw(timestamp.filter(lambda t: t.year < 9999))
    results = draw(st.lists(test_result, max_size=SMALL, unique_by=lambda r: r.node_id))
    return VerificationRun(
        plan_id=draw(content_id("pl")),
        command=tuple(draw(st.lists(text, min_size=1, max_size=4))),
        exit_code=draw(st.none() | st.integers(-(2**31), 2**31)),
        started_at=started,
        finished_at=started + timedelta(seconds=draw(st.integers(0, 10**6))),
        duration_ms=draw(st.integers(0, 10**9)),
        tool=draw(tool),
        results=tuple(sorted(results, key=lambda r: r.node_id)),
        timed_out=draw(st.booleans()),
    )


# --------------------------------------------------------------------------- evidence / policy


@st.composite
def evidence(draw: Any) -> Evidence:
    epistemic = draw(st.sampled_from(Epistemic))
    confidence = (
        Confidence.CERTAIN if epistemic is Epistemic.FACT else draw(st.sampled_from(Confidence))
    )
    return Evidence(
        id=draw(evidence_id),
        type=draw(st.sampled_from(EvidenceType)),
        producer=draw(nonempty_text),
        producer_version=draw(nonempty_text),
        subject=draw(nonempty_text),
        result=draw(st.sampled_from(EvidenceResult)),
        source_location=draw(st.none() | source_location()),
        collected_at=draw(timestamp),
        confidence=confidence,
        epistemic=epistemic,
        limitations=tuple(draw(st.lists(text, max_size=3))),
        data=draw(frozen_map),
    )


claim = st.builds(
    Claim,
    id=content_id("cl"),
    statement_kind=st.sampled_from(ClaimKind),
    subject=nonempty_text,
    evidence_ids=st.lists(evidence_id, min_size=1, max_size=SMALL, unique=True).map(
        lambda v: tuple(sorted(v))
    ),
    status=st.sampled_from(ClaimStatus),
)
limitation = st.builds(
    Limitation, kind=nonempty_text, message=nonempty_text, subject=st.none() | nonempty_text
)
scope = st.builds(
    Scope,
    paths=st.lists(nonempty_text, max_size=3).map(tuple),
    symbols=st.lists(nonempty_text, max_size=3).map(tuple),
)
requirement = st.builds(
    Requirement,
    id=nonempty_text,
    kind=nonempty_text,
    level=st.sampled_from(RequirementLevel),
    select=st.none() | scope,
)
invariant = st.builds(
    Invariant,
    id=st.from_regex(r"[a-z0-9][a-z0-9-]{1,20}", fullmatch=True),
    description=nonempty_text,
    scope=scope,
    level=st.sampled_from(RequirementLevel),
    requires=st.lists(requirement, min_size=1, max_size=3).map(tuple),
)
policy = st.builds(
    Policy,
    name=nonempty_text,
    requirements=st.lists(requirement, max_size=SMALL, unique_by=lambda r: r.id).map(
        lambda v: tuple(sorted(v, key=lambda r: r.id))
    ),
    require_complete_analysis=st.booleans(),
)
requirement_result = st.builds(
    RequirementResult,
    requirement_id=nonempty_text,
    kind=nonempty_text,
    level=st.sampled_from(RequirementLevel),
    status=st.sampled_from(RequirementStatus),
    evidence_ids=evidence_ids,
    message=st.none() | text,
)
requirement_results = st.lists(
    requirement_result, max_size=SMALL, unique_by=lambda r: r.requirement_id
).map(lambda v: tuple(sorted(v, key=lambda r: r.requirement_id)))
policy_result = st.builds(
    PolicyResult,
    verdict=st.sampled_from(Verdict),
    requirement_results=requirement_results,
    summary=text,
)

# --------------------------------------------------------------------------- proof / plugin

remote_url = st.none() | st.one_of(
    identifier.map(lambda n: f"https://example.invalid/{n}.git"),
    identifier.map(lambda n: f"git@example.invalid:{n}.git"),
    identifier.map(lambda n: f"ssh://git@example.invalid/{n}.git"),
)
proof_subject = st.builds(
    ProofSubject,
    remote_url=remote_url,
    base=revision(commit_required=True),
    head=revision(),
    is_dirty=st.booleans(),
)
proof_environment = st.builds(
    ProofEnvironment,
    rift_version=nonempty_text,
    python_version=nonempty_text,
    platform=nonempty_text,
    git_version=nonempty_text,
    tools=st.lists(tool, max_size=3, unique_by=lambda t: t.name).map(
        lambda v: tuple(sorted(v, key=lambda t: t.name))
    ),
    config_hash=hex_string(64),
    hostname=st.none() | nonempty_text,
)
proof_verification = st.builds(
    ProofVerification,
    plan=st.none() | verification_plan(),
    runs=st.lists(verification_run(), max_size=2).map(tuple),
)
proof_policy = st.builds(
    ProofPolicy, policy_hash=hex_string(64), requirement_results=requirement_results
)
plugin_metadata = st.builds(
    PluginMetadata,
    name=nonempty_text,
    version=nonempty_text,
    api_version=st.integers(1, 10),
    kind=st.sampled_from(PluginKind),
    capabilities=st.lists(nonempty_text, max_size=3).map(tuple),
    file_patterns=st.lists(nonempty_text, max_size=3).map(tuple),
    requirements=st.lists(nonempty_text, max_size=3).map(tuple),
)
change_proof = st.builds(
    ChangeProof,
    schema_version=st.sampled_from(["0.1.0", "1.0.0", "0.12.3"]),
    proof_id=content_id("rp"),
    generated_at=timestamp,
    subject=proof_subject,
    environment=proof_environment,
    changes=st.lists(changed_file(), max_size=2, unique_by=lambda f: f.path).map(sorted_files),
    claims=st.lists(claim, max_size=2, unique_by=lambda c: c.id).map(
        lambda v: tuple(sorted(v, key=lambda c: c.id))
    ),
    evidence=st.lists(evidence(), max_size=2, unique_by=lambda e: e.id).map(
        lambda v: tuple(sorted(v, key=lambda e: e.id))
    ),
    verification=proof_verification,
    limitations=st.lists(limitation, max_size=2).map(tuple),
    policy=proof_policy,
    verdict=st.sampled_from(Verdict),
)

MODEL_STRATEGIES: dict[type[Any], st.SearchStrategy[Any]] = {
    Repository: repository,
    RepositoryRevision: revision(),
    LineRange: line_range,
    ChangedFile: changed_file(),
    ChangeSet: change_set(),
    SourceLocation: source_location(),
    Symbol: symbol(),
    Node: node(),
    Edge: edge,
    UnresolvedReference: unresolved,
    Diagnostic: diagnostic(),
    AnalysisResult: analysis_result(),
    ImpactExplanation: explanation(),
    ImpactResult: impact_result(),
    PlannedCheck: planned_check,
    ExclusionRecord: exclusion,
    VerificationPlan: verification_plan(),
    ToolInfo: tool,
    TestResult: test_result,
    VerificationRun: verification_run(),
    Evidence: evidence(),
    Claim: claim,
    Limitation: limitation,
    Scope: scope,
    Requirement: requirement,
    Invariant: invariant,
    Policy: policy,
    RequirementResult: requirement_result,
    PolicyResult: policy_result,
    ProofSubject: proof_subject,
    ProofEnvironment: proof_environment,
    ProofVerification: proof_verification,
    ProofPolicy: proof_policy,
    PluginMetadata: plugin_metadata,
    ChangeProof: change_proof,
}
