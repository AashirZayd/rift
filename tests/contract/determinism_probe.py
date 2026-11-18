"""Subprocess probe for tests/contract/test_determinism.py (not collected: no test_ prefix).

Usage: python determinism_probe.py <repo> <base-commit>

Prints one JSON object: ``output`` holds canonical JSON + content IDs for a real-git ChangeSet and a
fixed corpus of models built partly by iterating sets; ``set_order`` is a raw set iteration, which
must differ across PYTHONHASHSEED values (proving the seed really changes hashing).
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rift.core.canonical import canonical_json
from rift.core.frozen import FrozenMap
from rift.core.identity import evidence_id, plan_id
from rift.core.ids import EvidenceId, NodeId, file_node_id
from rift.core.models.evidence import Epistemic, Evidence, EvidenceResult, EvidenceType
from rift.core.models.graph import Confidence, Edge, EdgeFlag, EdgeKind, EdgeOrigin
from rift.core.models.verification import ExclusionRecord, PlanMode, VerificationPlan
from rift.core.serialization import to_dict
from rift.git.changes import detect_changes
from rift.git.repository import open_repository

WORDS = {f"word-{i:03d}" for i in range(64)}


def main(repo_path: str, base: str) -> None:
    repo = open_repository(Path(repo_path))
    pieces: list[str] = [
        canonical_json(to_dict(detect_changes(repo, base=base, head="HEAD"))),
        canonical_json(to_dict(detect_changes(repo, base=base))),
    ]
    # Built by iterating sets: the models must canonicalize regardless of iteration order.
    data = FrozenMap.of({word: len(word) for word in WORDS})
    evidence = Evidence(
        id=EvidenceId("ev_" + "0" * 24),
        type=EvidenceType.GIT_CHANGE,
        producer="probe",
        producer_version="1",
        subject="file:a.py",
        result=EvidenceResult.INFO,
        source_location=None,
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        confidence=Confidence.CERTAIN,
        epistemic=Epistemic.FACT,
        limitations=(),
        data=data,
    )
    edge = Edge(
        source=file_node_id("a.py"),
        target=file_node_id("b.py"),
        kind=EdgeKind.IMPORTS,
        origin=EdgeOrigin.AST_STATIC,
        confidence=Confidence.LIKELY,
        evidence_ids=tuple(sorted({evidence_id(evidence)})),
        flags=frozenset(set(EdgeFlag)),
    )
    plan = VerificationPlan(
        mode=PlanMode.SELECTIVE,
        selected=(),
        excluded=tuple(
            ExclusionRecord(node_id=NodeId(f"test:tests/{w}.py::t"), reason="not affected")
            for w in sorted(WORDS)
        ),
        full_suite_recommended=False,
        reasons=(),
    )
    pieces += [
        canonical_json(to_dict(evidence)),
        evidence_id(evidence),
        canonical_json(to_dict(edge)),
        canonical_json(to_dict(plan)),
        plan_id(plan),
    ]
    sys.stdout.write(json.dumps({"output": pieces, "set_order": list(WORDS)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
