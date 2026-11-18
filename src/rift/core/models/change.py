"""Changes between two revisions (SPEC §6.1)."""

from dataclasses import dataclass
from enum import StrEnum

from rift.core.models._checks import check_types, require
from rift.core.models.repo import RepositoryRevision
from rift.core.paths import validate_repo_path


class ChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"


_NEEDS_OLD_PATH = frozenset({ChangeStatus.RENAMED, ChangeStatus.COPIED})


@dataclass(frozen=True, slots=True, kw_only=True, order=True)
class LineRange:
    """1-based inclusive line range in the new file (old file for deletions)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        check_types(self)
        require(
            isinstance(self.start, int) and not isinstance(self.start, bool) and self.start >= 1,
            "start must be >= 1",
        )
        require(
            isinstance(self.end, int) and not isinstance(self.end, bool) and self.end >= self.start,
            "end must be >= start",
        )


def merge_line_ranges(ranges: list[LineRange] | tuple[LineRange, ...]) -> tuple[LineRange, ...]:
    """Sort and merge overlapping or adjacent ranges into canonical form."""
    merged: list[LineRange] = []
    for current in sorted(ranges):
        if merged and current.start <= merged[-1].end + 1:
            last = merged[-1]
            merged[-1] = LineRange(start=last.start, end=max(last.end, current.end))
        else:
            merged.append(current)
    return tuple(merged)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangedFile:
    path: str
    old_path: str | None
    status: ChangeStatus
    changed_lines: tuple[LineRange, ...]
    is_binary: bool

    def __post_init__(self) -> None:
        check_types(self)
        validate_repo_path(self.path, "path")
        if self.status in _NEEDS_OLD_PATH:
            require(self.old_path is not None, f"{self.status} requires old_path")
            validate_repo_path(self.old_path or "", "old_path")
        else:
            require(self.old_path is None, f"{self.status} must not have old_path")
        require(
            self.changed_lines == merge_line_ranges(self.changed_lines),
            "changed_lines must be sorted, non-overlapping and non-adjacent",
        )
        require(not (self.is_binary and self.changed_lines), "binary files have no line ranges")


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeSet:
    """All files changed from ``base`` (a commit) to ``head`` (a commit or the working tree)."""

    base: RepositoryRevision
    head: RepositoryRevision
    files: tuple[ChangedFile, ...]
    includes_uncommitted: bool

    def __post_init__(self) -> None:
        check_types(self)
        require(self.base.commit is not None, "base must be a commit")
        require(
            self.includes_uncommitted == (self.head.commit is None),
            "includes_uncommitted must be true exactly when head is the working tree",
        )
        paths = [changed.path for changed in self.files]
        require(paths == sorted(set(paths)), "files must be sorted by path with unique paths")
