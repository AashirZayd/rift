"""Change detection: commit↔commit and commit↔working-tree diffs (SPEC §8).

Determinism and honesty rules (ADR-0006):

- Every diff pins the options user config could otherwise change (algorithm, rename detection and
  limit, inter-hunk context, relative paths, submodule handling).
- Binary-ness is decided from content (a NUL byte in the first 8000 bytes of either side, git's
  own heuristic), never from ``.gitattributes`` or ``core.attributesFile``: attributes are
  repository- or user-controlled, and could otherwise hide line-level changes or make results
  machine-dependent. Text diffs therefore run with ``--text``.
- Committed content is addressed by blob ID, not pathspec (Git for Windows rewrites ``\\`` in
  pathspecs, even ``:(literal)``). Pathspecs are used only for working-tree content git has not
  hashed, where such names cannot exist on that platform.
- Submodules are compared by their recorded commit only (``--ignore-submodules=dirty``), so git
  never runs inside a submodule, whose own config RIFT does not control.
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
    parse_raw_z,
    parse_untracked_z,
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
        "--ignore-submodules=dirty",
    )


def _literal(path: str) -> str:
    """Pathspec that matches exactly ``path`` (no glob or magic interpretation)."""
    return f":(literal){path}"


def _whole_file(line_count: int) -> tuple[LineRange, ...]:
    return () if line_count == 0 else (LineRange(start=1, end=line_count),)


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_SNIFF_BYTES]


@dataclass(frozen=True, slots=True, kw_only=True)
class _Content:
    """What RIFT needs to know about one side of a change."""

    is_binary: bool
    line_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _DiffContext:
    repo: Repository
    revs: tuple[str, ...]
    options: tuple[str, ...]
    extra: tuple[str, ...]
    timeout: float
    worktree: bool  # head is the working tree: "new" blob IDs may name objects never written

    def run(self, args: list[str]) -> bytes:
        result = run_git(args, cwd=self.repo.root, timeout=self.timeout, extra_config=self.extra)
        return result.stdout

    def blob(self, sha: str) -> _Content:
        try:
            data = self.run(["cat-file", "blob", sha])
        except GitError as exc:
            if exc.code is not ErrorCode.GIT_FAILED:
                raise
            # The ID came from git's own tree diff, so a failure means the object is not in the
            # local store (partial clone: RIFT never lazily fetches, see runner.NO_NETWORK_ARGS).
            raise GitError(
                f"object {sha} is not available locally (partial or incomplete clone)",
                code=ErrorCode.MISSING_OBJECTS,
                hint="RIFT never fetches; use a full clone, or `fetch-depth: 0` without `filter:` "
                "in actions/checkout",
                stderr_tail=exc.stderr_tail,
            ) from exc
        return _Content(is_binary=_is_binary(data), line_count=count_lines(data))


def _worktree(repo: Repository, path: str) -> _Content:
    """A working-tree file, read safely.

    Never follows a symlink (lstat; the link itself is one line), never opens a non-regular file
    (a FIFO would block), and streams so large files are not loaded into memory. A missing file
    has no lines; an unreadable one raises :class:`GitError` (``unreadable_file``).
    """
    full = repo.root / path
    try:
        info = full.lstat()
    except FileNotFoundError:
        return _Content(is_binary=False, line_count=0)
    except OSError as exc:
        raise _unreadable(path, exc) from exc
    if stat.S_ISLNK(info.st_mode):
        return _Content(is_binary=False, line_count=1)
    if not stat.S_ISREG(info.st_mode):
        return _Content(is_binary=False, line_count=0)
    try:
        return _sniff(full)
    except OSError as exc:  # permission denied, or swapped for a symlink (O_NOFOLLOW → ELOOP)
        raise _unreadable(path, exc) from exc


def _unreadable(path: str, exc: OSError) -> GitError:
    return GitError(
        f"cannot read working-tree file {path!r}: {exc.strerror or exc}",
        code=ErrorCode.UNREADABLE_FILE,
        hint="make the file readable, or commit/stash it, and run again",
    )


def _sniff(full: Path) -> _Content:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(full, flags)
    with os.fdopen(fd, "rb") as handle:
        first = handle.read(_BINARY_SNIFF_BYTES)
        if _is_binary(first):
            return _Content(is_binary=True, line_count=0)
        newlines = first.count(b"\n")
        last = first[-1:]
        while chunk := handle.read(_READ_CHUNK):
            newlines += chunk.count(b"\n")
            last = chunk[-1:]
    if not last:
        return _Content(is_binary=False, line_count=0)
    return _Content(is_binary=False, line_count=newlines + (0 if last == b"\n" else 1))


def _changed_file(ctx: _DiffContext, change: RawChange) -> ChangedFile:
    is_binary, ranges = _describe(ctx, change)
    return ChangedFile(
        path=change.path,
        old_path=change.old_path,
        status=change.status,
        changed_lines=() if is_binary else ranges,
        is_binary=is_binary,
    )


def _describe(ctx: _DiffContext, change: RawChange) -> tuple[bool, tuple[LineRange, ...]]:
    """(is_binary, changed line ranges) for one raw change."""
    if change.is_gitlink:
        return False, ()  # a submodule pointer has no lines
    old = ctx.blob(change.old_sha) if change.old_sha else None
    if change.status is ChangeStatus.DELETED:
        if old is None:
            return False, ()
        return old.is_binary, _whole_file(old.line_count)
    # In working-tree mode the new side is whatever is on disk. git may report a blob ID it hashed
    # in memory (e.g. for rename detection) without ever writing it, so never cat-file it.
    new_sha = None if ctx.worktree else change.new_sha
    new = ctx.blob(new_sha) if new_sha else _worktree(ctx.repo, change.path)
    if change.status is ChangeStatus.ADDED or old is None:
        return new.is_binary, _whole_file(new.line_count)
    if old.is_binary or new.is_binary:
        return True, ()
    if change.old_sha and new_sha:
        patch = ctx.run(["diff", "-U0", "--text", *ctx.options, change.old_sha, new_sha])
    else:
        paths = [change.path] if change.old_path is None else [change.old_path, change.path]
        patch = ctx.run(
            ["diff", "-U0", "--text", *ctx.options, *ctx.revs, "--", *map(_literal, paths)]
        )
    return False, parse_hunk_ranges(patch, old_side=False)


def _untracked(repo: Repository, path: str, *, is_directory: bool) -> ChangedFile:
    """An untracked file is wholly added: lines 1..N of its current content.

    ``is_directory``: git lists an untracked nested repository as ``dir/`` without descending into
    it; it is reported as one added entry with no lines.
    """
    content = _Content(is_binary=False, line_count=0) if is_directory else _worktree(repo, path)
    return ChangedFile(
        path=path,
        old_path=None,
        status=ChangeStatus.ADDED,
        changed_lines=() if content.is_binary else _whole_file(content.line_count),
        is_binary=content.is_binary,
    )


def _merge_untracked(files: list[ChangedFile], untracked: list[ChangedFile]) -> list[ChangedFile]:
    """Add untracked entries; a path that is deleted in the index but present again on disk
    (``git rm --cached a.py``, or ``git rm`` then re-create) becomes one MODIFIED entry covering
    the whole current content (conservative: every line may have changed)."""
    by_path = {changed.path: changed for changed in files}
    for added in untracked:
        existing = by_path.get(added.path)
        if existing is None:
            by_path[added.path] = added
        elif existing.status is ChangeStatus.DELETED:
            by_path[added.path] = ChangedFile(
                path=added.path,
                old_path=None,
                status=ChangeStatus.MODIFIED,
                changed_lines=added.changed_lines,
                is_binary=added.is_binary,
            )
        else:  # pragma: no cover - git never lists a tracked path as untracked
            raise GitError(
                f"path reported both as tracked change and untracked: {added.path!r}",
                code=ErrorCode.UNEXPECTED_GIT_OUTPUT,
            )
    return list(by_path.values())


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
    ctx = _DiffContext(
        repo=repo,
        revs=revs,
        options=options,
        extra=extra,
        timeout=timeout,
        worktree=head_commit is None,
    )

    raw = ctx.run(["diff", "--raw", "-z", "--no-abbrev", *options, *revs, "--"])
    files = [_changed_file(ctx, change) for change in parse_raw_z(raw)]

    if head_commit is None:
        listing = ctx.run(["ls-files", "--others", "--exclude-standard", "-z", "--"])
        untracked = [
            _untracked(repo, path, is_directory=is_directory)
            for path, is_directory in parse_untracked_z(listing)
        ]
        files = _merge_untracked(files, untracked)
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
