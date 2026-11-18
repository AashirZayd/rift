"""The Change Proof document model (SPEC §6.4, §16). Builder/validator/schema are M6."""

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from rift.core.clock import check_timestamp
from rift.core.ids import IdPrefix, validate_content_id
from rift.core.models._checks import check_types, is_hex, non_empty, require, sorted_unique
from rift.core.models.change import ChangedFile
from rift.core.models.evidence import Claim, Evidence, Limitation
from rift.core.models.policy import RequirementResult, Verdict
from rift.core.models.repo import RepositoryRevision
from rift.core.models.verification import ToolInfo, VerificationPlan, VerificationRun

PROTOCOL_VERSION = "0.1.0"
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def url_has_credentials(url: str) -> bool:
    """True if ``url`` carries secrets in its userinfo (SPEC §21: strip URL credentials).

    Any password is a secret. For http(s) a bare username is treated as one too, because tokens
    are commonly passed that way (``https://<token>@host/repo``). ``ssh://git@host`` and the
    scp form ``git@host:repo`` name a login, not a secret, and are allowed.
    """
    parts = urlsplit(url)
    if parts.password:
        return True
    return parts.scheme.lower() in {"http", "https"} and bool(parts.username)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProofSubject:
    remote_url: str | None
    base: RepositoryRevision
    head: RepositoryRevision
    is_dirty: bool

    def __post_init__(self) -> None:
        check_types(self)
        if self.remote_url is not None:
            non_empty(self.remote_url, "remote_url")
            require(
                not url_has_credentials(self.remote_url), "remote_url must not carry credentials"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProofEnvironment:
    rift_version: str
    python_version: str
    platform: str
    git_version: str
    tools: tuple[ToolInfo, ...]
    config_hash: str
    hostname: str | None = None  # opt-in only (SPEC §7.4); volatile

    def __post_init__(self) -> None:
        check_types(self)
        for name in ("rift_version", "python_version", "platform", "git_version"):
            non_empty(getattr(self, name), name)
        require(is_hex(self.config_hash, (64,)), "config_hash must be a sha256 hex digest")
        sorted_unique([tool.name for tool in self.tools], "tools")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProofVerification:
    plan: VerificationPlan | None
    runs: tuple[VerificationRun, ...]

    def __post_init__(self) -> None:
        check_types(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProofPolicy:
    policy_hash: str
    requirement_results: tuple[RequirementResult, ...]

    def __post_init__(self) -> None:
        check_types(self)
        require(is_hex(self.policy_hash, (64,)), "policy_hash must be a sha256 hex digest")
        sorted_unique(
            [result.requirement_id for result in self.requirement_results], "requirement_results"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeProof:
    schema_version: str
    proof_id: str
    generated_at: datetime
    subject: ProofSubject
    environment: ProofEnvironment
    changes: tuple[ChangedFile, ...]
    claims: tuple[Claim, ...]
    evidence: tuple[Evidence, ...]
    verification: ProofVerification
    limitations: tuple[Limitation, ...]
    policy: ProofPolicy
    verdict: Verdict

    def __post_init__(self) -> None:
        check_types(self)
        require(
            isinstance(self.schema_version, str)
            and _SEMVER.fullmatch(self.schema_version) is not None,
            "schema_version must be SemVer MAJOR.MINOR.PATCH",
        )
        validate_content_id(self.proof_id, IdPrefix.PROOF)
        check_timestamp(self.generated_at, "generated_at")
        paths = [changed.path for changed in self.changes]
        require(paths == sorted(set(paths)), "changes must be sorted by path, unique")
        sorted_unique([claim.id for claim in self.claims], "claims")
        sorted_unique([item.id for item in self.evidence], "evidence")
