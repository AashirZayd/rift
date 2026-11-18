"""Enumerate the Python source files of a repository without following links (ADR-0008).

The walk is iterative (deep trees cannot hit the recursion limit) and visits entries in sorted
order. Symlinks and Windows junctions are never followed and are reported as limitations, like
everything else that is skipped for a reason other than a configured or default exclusion.
"""

import errno
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from rift.core.errors import ErrorCode, RiftError
from rift.core.models.analysis import limitation_sort_key, sort_unique
from rift.core.models.evidence import Limitation
from rift.core.paths import is_repo_path

# Directory names never descended into (besides any name starting with "."): caches and
# dependency trees, never first-party source. Virtual environments are recognised by their
# ``pyvenv.cfg`` marker instead of by name, so a package called ``env`` is still analyzed.
_EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "node_modules"})
_VENV_MARKER = "pyvenv.cfg"
# Windows reparse tags that redirect elsewhere (other tags, e.g. cloud placeholders, are files).
_LINK_REPARSE_TAGS = frozenset(
    tag
    for tag in (
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
    )
    if tag is not None
)

LIMITATION_SYMLINK = "symlink_skipped"
LIMITATION_UNREADABLE = "unreadable_path"
LIMITATION_UNREPRESENTABLE = "unrepresentable_path"
LIMITATION_OUTSIDE_ROOT = "outside_root"


class DiscoveryError(RiftError):
    """The discovery root itself is unusable (not an existing directory)."""

    default_code = ErrorCode.INVALID_ARGUMENT


@dataclass(frozen=True, slots=True, kw_only=True)
class DiscoveredFiles:
    """``files``: sorted repo-relative POSIX paths of ``*.py`` files. ``limitations``: what was
    skipped and why, sorted by ``(kind, subject, message)`` without duplicates."""

    files: tuple[str, ...]
    limitations: tuple[Limitation, ...]


def matches_exclude(path: str, patterns: Sequence[str]) -> bool:
    """Glob match on a repo-relative path. ``*`` crosses ``/`` (fnmatch); a leading ``**/`` also
    matches at the top level, so ``**/migrations/**`` excludes ``migrations/x.py``."""
    for pattern in patterns:
        if fnmatchcase(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
            return True
    return False


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    if is_junction is not None and is_junction():
        return True
    if not _LINK_REPARSE_TAGS:
        return False
    tag = getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0)
    return tag in _LINK_REPARSE_TAGS


def _is_venv(path: str) -> bool:
    try:
        info = os.lstat(os.path.join(path, _VENV_MARKER))  # noqa: PTH118  # DirEntry paths are str
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode)


def _excluded_dir(name: str, rel: str, full: str, exclude: Sequence[str]) -> bool:
    if name.startswith(".") or name in _EXCLUDED_DIR_NAMES or name.endswith(".egg-info"):
        return True
    return matches_exclude(rel + "/", exclude) or matches_exclude(rel, exclude) or _is_venv(full)


def _representable(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:  # undecodable bytes on POSIX surface as lone surrogates
        return False
    return True


def discover_python_files(root: Path, *, exclude: Sequence[str] = ()) -> DiscoveredFiles:
    """Every ``*.py`` regular file under ``root`` that is not excluded. Never follows links."""
    if not root.is_absolute():
        raise DiscoveryError(f"discovery root must be absolute: {root.name!r}")
    if not root.is_dir():
        raise DiscoveryError("discovery root is not an existing directory")
    real_root = root.resolve()
    walk = _Walk(exclude=tuple(exclude))
    stack: list[str] = [""]  # repo-relative directory paths ("" = root)
    while stack:
        rel_dir = stack.pop()
        try:
            with os.scandir(root / rel_dir if rel_dir else root) as iterator:
                entries = sorted(iterator, key=lambda e: e.name)
        except OSError as exc:
            walk.skip(LIMITATION_UNREADABLE, rel_dir or ".", _os_reason(exc))
            continue
        subdirs = [rel for entry in entries if (rel := walk.entry(entry, rel_dir)) is not None]
        for rel in reversed(subdirs):  # pop order = sorted order (output is sorted anyway)
            if (root / rel).resolve().is_relative_to(real_root):
                stack.append(rel)
            else:  # a bind mount or another redirect that is not a link
                walk.skip(LIMITATION_OUTSIDE_ROOT, rel, "resolves outside root")
    return DiscoveredFiles(
        files=tuple(sorted(walk.files)),
        limitations=sort_unique(walk.limitations, limitation_sort_key),
    )


@dataclass(slots=True)
class _Walk:
    exclude: tuple[str, ...]
    files: list[str] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)

    def skip(self, kind: str, subject: str, reason: str) -> None:
        self.limitations.append(_limit(kind, subject, reason))

    def entry(self, entry: os.DirEntry[str], rel_dir: str) -> str | None:
        """Record a file or a skip; return the entry's path if it is a directory to descend."""
        if not _representable(entry.name):
            self.skip(LIMITATION_UNREPRESENTABLE, rel_dir or ".", "a name is not valid UTF-8")
            return None
        rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
        if not is_repo_path(rel):
            self.skip(LIMITATION_UNREPRESENTABLE, rel_dir or ".", "a name is not a valid path")
            return None
        try:
            if _is_link(entry):
                if not matches_exclude(rel, self.exclude):
                    self.skip(LIMITATION_SYMLINK, rel, "links are not followed")
                return None
            if entry.is_dir(follow_symlinks=False):
                excluded = _excluded_dir(entry.name, rel, entry.path, self.exclude)
                return None if excluded else rel
            if (
                entry.name.endswith(".py")
                and entry.is_file(follow_symlinks=False)
                and not matches_exclude(rel, self.exclude)
            ):
                self.files.append(rel)
        except OSError as exc:
            self.skip(LIMITATION_UNREADABLE, rel, _os_reason(exc))
        return None


def _limit(kind: str, subject: str, reason: str) -> Limitation:
    return Limitation(kind=kind, message=f"skipped: {reason}", subject=subject)


def _os_reason(exc: OSError) -> str:
    """The errno name only: ``strerror`` text is localized and may embed absolute paths."""
    return errno.errorcode.get(exc.errno or 0, "os error")
