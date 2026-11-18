"""Content-derived identifiers for models (SPEC §7.3, §7.4, §13).

An ID is ``<prefix>_<24 hex>`` over the canonical JSON of the model's serialized form, with the ID
field itself removed and volatile fields (timestamps, durations, hostname) stripped. Two runs that
differ only in volatile fields therefore produce identical IDs.
"""

from dataclasses import replace

from rift.core.ids import EvidenceId, IdPrefix, content_id
from rift.core.models.evidence import Claim, Evidence
from rift.core.models.proof import ChangeProof
from rift.core.models.verification import VerificationPlan
from rift.core.serialization import to_dict


def evidence_id(evidence: Evidence) -> EvidenceId:
    payload = to_dict(evidence)
    del payload["id"]
    return EvidenceId(content_id(IdPrefix.EVIDENCE, payload))


def claim_id(claim: Claim) -> str:
    payload = to_dict(claim)
    del payload["id"]
    return content_id(IdPrefix.CLAIM, payload)


def plan_id(plan: VerificationPlan) -> str:
    return content_id(IdPrefix.PLAN, to_dict(plan))


def proof_id(proof: ChangeProof) -> str:
    payload = to_dict(proof)
    del payload["proof_id"]
    return content_id(IdPrefix.PROOF, payload)


def with_evidence_id(evidence: Evidence) -> Evidence:
    """Return ``evidence`` with its ``id`` replaced by the content-derived one."""
    return replace(evidence, id=evidence_id(evidence))


def with_claim_id(claim: Claim) -> Claim:
    return replace(claim, id=claim_id(claim))


def with_proof_id(proof: ChangeProof) -> ChangeProof:
    return replace(proof, proof_id=proof_id(proof))
