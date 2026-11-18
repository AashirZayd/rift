"""Repository discovery and the current revision (SPEC §8)."""

from pathlib import Path

from rift.core.errors import ErrorCode
from rift.core.models.repo import Repository, RepositoryRevision
from rift.git.errors import GitError, NotARepositoryError
from rift.git.parsing import parse_filter_drivers
from rift.git.runner import DEFAULT_TIMEOUT, run_git
from rift.git.version import require_supported_git

# Config scopes controlled by the repository itself (an extracted archive, a copied checkout).
# The user's own global/system config is trusted (SPEC §21.1), e.g. a global git-lfs filter.
_REPO_SCOPES = frozenset({"local", "worktree"})


def _text(stdout: bytes) -> str:
    try:
        return stdout.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise GitError(
            f"git output is not valid UTF-8: {stdout[:80]!r}",
            code=ErrorCode.UNDECODABLE_PATH,
            hint="rename the branch or directory to a UTF-8 name",
        ) from exc


def open_repository(path: Path, *, timeout: float = DEFAULT_TIMEOUT) -> Repository:
    """Discover the working tree containing ``path`` and check git is supported."""
    require_supported_git(timeout=timeout)
    if not path.is_dir():
        raise NotARepositoryError(f"not a directory: {path}")
    top = run_git(["rev-parse", "--show-toplevel"], cwd=path, timeout=timeout)
    root = Path(_text(top.stdout))
    if not root.is_absolute():
        raise GitError(
            f"git reported a relative top-level: {root}", code=ErrorCode.UNEXPECTED_GIT_OUTPUT
        )
    shallow = run_git(["rev-parse", "--is-shallow-repository"], cwd=root, timeout=timeout)
    return Repository(
        root=root,
        is_shallow=_text(shallow.stdout) == "true",
        default_branch=_default_branch(root, timeout=timeout),
    )


def _default_branch(root: Path, *, timeout: float) -> str | None:
    """``origin/HEAD`` → branch name, if the remote HEAD is known locally."""
    result = run_git(
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    if result.returncode != 0:
        return None
    short = _text(result.stdout)
    _, _, branch = short.partition("/")
    return branch or None


def repository_config_overrides(root: Path, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, ...]:
    """``-c`` overrides that neutralize filter drivers defined by the repository's own config.

    Filter ``clean`` drivers run during working-tree comparisons even with the SPEC §8 hardening
    (verified by tests/security). Reading config executes nothing. Names containing ``=`` cannot be
    expressed as ``-c key=value`` on git 2.30, so such a repository is refused (ADR-0006).
    """
    result = run_git(
        ["config", "--show-scope", "--includes", "-z", "--get-regexp", r"^filter\."],
        cwd=root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    overrides: list[str] = []
    for name in parse_filter_drivers(result.stdout, _REPO_SCOPES):
        if "=" in name:
            raise GitError(
                f"cannot neutralize repository filter driver {name!r}",
                code=ErrorCode.UNSAFE_REPOSITORY,
                hint="remove the filter from .git/config; RIFT will not run repository commands",
            )
        overrides.extend(
            (
                f"filter.{name}.clean=",
                f"filter.{name}.smudge=",
                f"filter.{name}.process=",
                f"filter.{name}.required=false",
            )
        )
    return tuple(overrides)


def is_dirty(
    repo: Repository, *, timeout: float = DEFAULT_TIMEOUT, overrides: tuple[str, ...] | None = None
) -> bool:
    """True if tracked files differ from HEAD/index or untracked (non-ignored) files exist."""
    extra = (
        repository_config_overrides(repo.root, timeout=timeout) if overrides is None else overrides
    )
    result = run_git(
        # --ignore-submodules=dirty: compare submodule commits only, never run git inside one.
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=dirty"],
        cwd=repo.root,
        timeout=timeout,
        extra_config=extra,
    )
    return result.stdout != b""


def head_commit(repo: Repository, *, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Full object name of HEAD, or None on an unborn branch."""
    result = run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}"],
        cwd=repo.root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    return _text(result.stdout) or None if result.returncode == 0 else None


def current_revision(repo: Repository, *, timeout: float = DEFAULT_TIMEOUT) -> RepositoryRevision:
    """HEAD: commit (None if unborn), branch (None if detached), dirty flag."""
    ref = run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=repo.root,
        timeout=timeout,
        ok_returncodes=(0, 1),
    )
    detached = ref.returncode != 0
    return RepositoryRevision(
        commit=head_commit(repo, timeout=timeout),
        ref=None if detached else _text(ref.stdout),
        is_detached=detached,
        is_dirty=is_dirty(repo, timeout=timeout),
    )
