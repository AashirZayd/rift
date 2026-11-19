"""The working tree's file listing as git sees it (ADR-0010 §1)."""

from rift.core.models.repo import Repository
from rift.git.parsing import parse_untracked_z
from rift.git.repository import repository_config_overrides
from rift.git.runner import DEFAULT_TIMEOUT, run_git


def list_worktree_files(repo: Repository, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[str, ...]:
    """Tracked files plus untracked files that are not ignored, sorted, repo-relative POSIX.

    ``ls-files --cached --others --exclude-standard``: a tracked file is listed even if an ignore
    rule matches it, and ``.gitignore``d files are not. An untracked nested repository is listed
    by git as a directory and is dropped, since it is not part of this repository. A tracked file
    deleted from disk is still listed; callers intersect with what exists. Repository filter
    drivers are neutralized as for every working-tree command (ADR-0006).
    """
    extra = repository_config_overrides(repo.root, timeout=timeout)
    result = run_git(
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--"],
        cwd=repo.root,
        timeout=timeout,
        extra_config=extra,
    )
    entries = parse_untracked_z(result.stdout)
    return tuple(sorted({path for path, is_directory in entries if not is_directory}))
