"""Change detection: commit↔commit and commit↔working-tree diffs (SPEC §8).

Determinism: every diff pins options that user config could otherwise change (algorithm, rename
detection and limit, inter-hunk context, relative paths), so the same repository state yields the
same ``ChangeSet`` regardless of ``~/.gitconfig`` (ADR-0006, tests/integration/git).

Line ranges are computed without pathspecs wherever possible: hunks come from a blob↔blob diff
and whole-file ranges from the blob itself. Git for Windows rewrites ``\\`` in pathspecs (even
``:(literal)``), so a pathspec cannot address every name a commit can contain. Pathspecs are used
only for working-tree content git has not hashed, where such names cannot exist on that platform.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from rift.core.errors import ErrorCode
from rift.core.models.change import ChangedFile, ChangeSet, ChangeStatus, LineRange
from rift.core.models.repo import Repository, RepositoryRevision
from rift.git.errors import GitError
from rift.git.parsing import (
    RawChange,
    count_lines,
    parse_hunk_ranges,
    parse_numstat_z,
    parse_paths_z,
    parse_raw_z,
)
from rift.git.repository import current_revision, repository_config_overrides
from rift.git.revisions import validate_revision
from rift.git.runner import DEFAULT_TIMEOUT, DIFF_ARGS, run_git

DEFAULT_RENAME_SIMILARITY = 50
_MAX_SIMILARITY = 100
_RENAME_LIMIT = 32767  # git's own ceiling; config (diff.renameLimit) must not change results
_BINARY_SNIFF_BYTES = 8000  # git's own heuristic: a NUL in the first 8000 bytes means binary
_READ_CHUNK = 1 << 16


def _diff_options(rename_similarity: int) -> tuple[str, ...]:
    if not 0 <= rename_similarity <= _MAX_SIMILARITY:
        raise GitError(
            f"rename_similarity must be 0..100, got {rename_similarity}",
            code=ErrorCode.INVALID_ARGUMENT,
        )
    return (
        *DIFF_ARGS,
        f"-M{rename_similarity}%",
        f"-l{_RENAME_LIMIT}",
        "--diff-algorithm=myers",
        "--indent-heuristic",
        "--inter-hunk-context=0",
        "--no-relative",
        "--ignore-submodules=none",
    )


def _literal(path: str) -> str:
    """Pathspec that matches exactly ``path`` (no glob or magic interpretation)."""
    return f":(literal){path}"


def _whole_file(line_count: int) -> tuple[LineRange, ...]:
    return () if line_count == 0 else (LineRange(start=1, end=line_count),)


@dataclass(frozen=True, slots=True, kw_only=True)
class _DiffContext:
    repo: Repository
    revs: tuple[str, ...]
    options: tuple[str, ...]
    extra: tuple[str, ...]
    timeout: float

    def run(self, args: list[str]) -> bytes:
        result = run_git(args, cwd=self.repo.root, timeout=self.timeout, extra_config=self.extra)
        return result.stdout

    def blob_lines(self, sha: str) -> tuple[LineRange, ...]:
        return _whole_file(count_lines(self.run(["cat-file", "blob", sha])))


def _line_ranges(ctx: _DiffContext, change: RawChange) -> tuple[LineRange, ...]:
    if change.is_gitlink:
        return ()  # a submodule pointer has no lines
    if change.status is ChangeStatus.DELETED:
        return ctx.blob_lines(change.old_sha) if change.old_sha else ()
    if change.status is ChangeStatus.ADDED:
        if change.new_sha:
            return ctx.blob_lines(change.new_sha)
        return _worktree_content(ctx.repo, change.path)[1]
    if change.old_sha and change.new_sha:
        patch = ctx.run(["diff", "-U0", *ctx.options, change.old_sha, change.new_sha])
        return parse_hunk_ranges(patch, old_side=False)
    paths = [change.path] if change.old_path is None else [change.old_path, change.path]
    patch = ctx.run(["diff", "-U0", *ctx.options, *ctx.revs, "--", *(_literal(p) for p in paths)])
    return parse_hunk_ranges(patch, old_side=False)


def _worktree_content(repo: Repository, path: str) -> tuple[bool, tuple[LineRange, ...]]:
    """(is_binary, whole-file ranges) of a working-tree file, read safely.

    Never follows a symlink (lstat; the link itself is one line) and never opens a non-regular
    file (a FIFO would block). A missing file has no lines.
    """
    full = repo.root / path
    try:
        info = full.lstat()
    except FileNotFoundError:
        return False, ()
    if stat.S_ISLNK(info.st_mode):
        return False, (LineRange(start=1, end=1),)
    if not stat.S_ISREG(info.st_mode):
        return False, ()
    is_binary, line_count = _sniff(full)
    return is_binary, () if is_binary else _whole_file(line_count)


def _sniff(full: Path) -> tuple[bool, int]:
    """(is_binary, line_count), streaming so large files are not loaded into memory."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(full, flags)
    with os.fdopen(fd, "rb") as handle:
        first = handle.read(_BINARY_SNIFF_BYTES)
        if b"\0" in first:
            return True, 0
        newlines = first.count(b"\n")
        last = first[-1:]
        while chunk := handle.read(_READ_CHUNK):
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
    if not last:
        return False, 0
    return False, newlines + (0 if last == b"\n" else 1)


def _untracked_file(repo: Repository, path: str) -> ChangedFile:
    """An untracked file is wholly added: lines 1..N of its current content."""
    is_binary, ranges = _worktree_content(repo, path)
    return ChangedFile(
        path=path,
        old_path=None,
        status=ChangeStatus.ADDED,
        changed_lines=ranges,
        is_binary=is_binary,
    )


def detect_changes(
    repo: Repository,
    *,
    base: str,
    head: str | None = None,
    rename_similarity: int = DEFAULT_RENAME_SIMILARITY,
    timeout: float = DEFAULT_TIMEOUT,
) -> ChangeSet:
    """Files changed from ``base`` to ``head``; ``head=None`` means the working tree.

    Working-tree mode includes staged and unstaged changes to tracked files, plus untracked files
    that are not ignored (``ls-files --others --exclude-standard``).
    """
    base_commit = validate_revision(repo, base, timeout=timeout)
    head_commit = None if head is None else validate_revision(repo, head, timeout=timeout)
    options = _diff_options(rename_similarity)
    extra = repository_config_overrides(repo.root, timeout=timeout) if head is None else ()
    revs = (base_commit,) if head_commit is None else (base_commit, head_commit)
    ctx = _DiffContext(repo=repo, revs=revs, options=options, extra=extra, timeout=timeout)

    raw = ctx.run(["diff", "--raw", "-z", "--no-abbrev", *options, *revs, "--"])
    binary = parse_numstat_z(ctx.run(["diff", "--numstat", "-z", *options, *revs, "--"]))

    files: list[ChangedFile] = []
    for change in parse_raw_z(raw):
        is_binary = binary.get(change.path, False)
        files.append(
            ChangedFile(
                path=change.path,
                old_path=change.old_path,
                status=change.status,
                changed_lines=() if is_binary else _line_ranges(ctx, change),
                is_binary=is_binary,
            )
        )

    if head_commit is None:
        untracked = ctx.run(["ls-files", "--others", "--exclude-standard", "-z", "--"])
        files.extend(_untracked_file(repo, path) for path in parse_paths_z(untracked))
        current = current_revision(repo, timeout=timeout)
        head_revision = RepositoryRevision(
            commit=None, ref=current.ref, is_detached=current.is_detached, is_dirty=current.is_dirty
        )
    else:
        head_revision = RepositoryRevision(
            commit=head_commit, ref=None, is_detached=False, is_dirty=False
        )

    return ChangeSet(
        base=RepositoryRevision(commit=base_commit, ref=None, is_detached=False, is_dirty=False),
        head=head_revision,
        files=tuple(sorted(files, key=lambda f: f.path)),
        includes_uncommitted=head_commit is None,
    )
