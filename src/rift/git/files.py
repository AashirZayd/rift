"""The working tree's file listing as git sees it (ADR-0010 §1)."""

from collections.abc import Sequence

from rift.core.errors import ErrorCode
from rift.core.models.repo import Repository
from rift.git.errors import GitError
from rift.git.parsing import decode_path, parse_untracked_z
from rift.git.repository import repository_config_overrides
from rift.git.revisions import check_revision_syntax
from rift.git.runner import DEFAULT_TIMEOUT, run_git

_REGULAR_MODES = frozenset({b"100644", b"100755"})


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


def read_files_at(
    repo: Repository, commit: str, paths: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, bytes]:
    """Contents of the regular files ``paths`` at ``commit`` (ADR-0010 §4). A path that is
    absent, a symlink, a submodule or a directory at that commit is left out.

    ``commit`` must already be validated (e.g. a ChangeSet's base). Paths are matched literally
    (``--literal-pathspecs``). Reading objects runs no filter or driver, and a partial clone is
    never fetched from (ADR-0006).
    """
    check_revision_syntax(commit)
    wanted = sorted(set(paths))
    if not wanted:
        return {}
    listing = run_git(
        [
            "--literal-pathspecs",
            "ls-tree",
            "-z",
            "--full-tree",
            "--end-of-options",
            commit,
            "--",
            *wanted,
        ],
        cwd=repo.root,
        timeout=timeout,
    ).stdout
    requested = frozenset(wanted)
    found: dict[str, bytes] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, kind, sha = meta.split(b" ")
        path = decode_path(raw_path)
        if kind != b"blob" or mode not in _REGULAR_MODES or path not in requested:
            continue
        try:
            found[path] = run_git(
                ["cat-file", "blob", sha.decode("ascii")], cwd=repo.root, timeout=timeout
            ).stdout
        except GitError as exc:
            if exc.code is not ErrorCode.GIT_FAILED:
                raise
            object_id = sha.decode("ascii")
            raise GitError(
                f"object {object_id} is not available locally (partial or incomplete clone)",
                code=ErrorCode.MISSING_OBJECTS,
                hint="RIFT never fetches; use a full clone, or `fetch-depth: 0` without `filter:` "
                "in actions/checkout",
                stderr_tail=exc.stderr_tail,
            ) from exc
    return found
