"""Repository identity and revisions (SPEC §6.1)."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rift.core.models._checks import check_types, is_hex, non_empty, require

# Object names: SHA-1 (40 hex) or SHA-256 (64 hex) repositories.
COMMIT_HEX_LENGTHS = (40, 64)


class Vcs(StrEnum):
    GIT = "git"


@dataclass(frozen=True, slots=True, kw_only=True)
class Repository:
    """A discovered repository.

    ``root`` is absolute and machine-specific: this model is for in-process use and must never be
    embedded in a proof (SPEC §7.7). Proofs identify the repository through ``ProofSubject``.
    """

    root: Path
    vcs: Vcs = Vcs.GIT
    is_shallow: bool
    default_branch: str | None

    def __post_init__(self) -> None:
        check_types(self)
        require(isinstance(self.root, Path) and self.root.is_absolute(), "root must be absolute")
        if self.default_branch is not None:
            non_empty(self.default_branch, "default_branch")


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryRevision:
    """A revision. ``commit=None`` means the working tree (or an unborn branch)."""

    commit: str | None
    ref: str | None
    is_detached: bool
    is_dirty: bool

    def __post_init__(self) -> None:
        check_types(self)
        if self.commit is not None:
            require(
                is_hex(self.commit, COMMIT_HEX_LENGTHS),
                f"commit must be a full lowercase object name, got {self.commit!r}",
            )
        if self.ref is not None:
            non_empty(self.ref, "ref")
        require(not (self.is_detached and self.ref is not None), "a detached HEAD has no ref")
