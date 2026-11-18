"""Revision validation and base resolution (SPEC §8)."""

import re

from rift.core.errors import ErrorCode
from rift.core.models.repo import COMMIT_HEX_LENGTHS, Repository
from rift.git.errors import GitError, InvalidRevisionError, ShallowHistoryError
from rift.git.runner import DEFAULT_TIMEOUT, run_git

# In a shallow clone only an object name or ancestry syntax (`main~5`, `HEAD^`) can point "beyond
# the fetched history"; an unknown branch name or a typo is reported as an invalid revision.
_OBJECT_NAME = re.compile(r"[0-9a-fA-F]{4,64}")
_SHALLOW_HINT = (
    "the clone is shallow; fetch more history (git fetch --deepen=N or git fetch --unshallow), "
    "or use `fetch-depth: 0` with actions/checkout"
)


def check_revision_syntax(rev: str) -> None:
    """Reject revision arguments that could be parsed as options or smuggle control characters."""
    if not isinstance(rev, str) or not rev:
        raise InvalidRevisionError("revision must be a non-empty string")
    if rev.startswith("-"):
        raise InvalidRevisionError(
            f"revision must not start with '-': {rev!r}",
            hint="revisions are never options; pass a branch, tag or commit",
        )
    if any(char in rev for char in "\x00\n\r"):
        raise InvalidRevisionError(f"revision contains control characters: {rev!r}")


def validate_revision(repo: Repository, rev: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Resolve ``rev`` to a full commit object name, or raise.

    Uses ``rev-parse --verify --end-of-options <rev>^{commit}`` so the argument can never be read as
    an option, even after the leading-dash check.
    """
    check_revision_syntax(rev)
    result = run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"],
        cwd=repo.root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    commit = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or not commit:
        if repo.is_shallow and (_OBJECT_NAME.fullmatch(rev) or "~" in rev or "^" in rev):
            raise ShallowHistoryError(
                f"revision {rev!r} is not available in this shallow clone", hint=_SHALLOW_HINT
            )
        raise InvalidRevisionError(f"unknown revision or not a commit: {rev!r}")
    if len(commit) not in COMMIT_HEX_LENGTHS:
        raise GitError(
            f"unexpected rev-parse output: {commit!r}", code=ErrorCode.UNEXPECTED_GIT_OUTPUT
        )
    return commit


def resolve_base(
    repo: Repository,
    *,
    explicit: str | None = None,
    default_base: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Base commit: ``--base`` → config ``git.default_base`` → ``merge-base HEAD origin/HEAD``."""
    if explicit is not None:
        return validate_revision(repo, explicit, timeout=timeout)
    if default_base is not None:
        return validate_revision(repo, default_base, timeout=timeout)
    has_origin_head = run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", "refs/remotes/origin/HEAD"],
        cwd=repo.root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    if has_origin_head.returncode == 0:
        merge_base = run_git(
            ["merge-base", "HEAD", "refs/remotes/origin/HEAD"],
            cwd=repo.root,
            timeout=timeout,
            ok_returncodes=(0, 1),
        )
        commit = merge_base.stdout.decode("ascii", errors="replace").strip()
        if merge_base.returncode == 0 and commit:
            return commit
        if repo.is_shallow:
            raise ShallowHistoryError(
                "no merge base with origin/HEAD in this shallow clone", hint=_SHALLOW_HINT
            )
        raise GitError(
            "HEAD and origin/HEAD have no common ancestor",
            code=ErrorCode.NO_BASE_REVISION,
            hint="pass --base REV",
        )
    raise GitError(
        "no base revision: none given, no git.default_base configured, and no origin/HEAD",
        code=ErrorCode.NO_BASE_REVISION,
        hint="pass --base REV or set git.default_base in .rift/config.toml",
    )
