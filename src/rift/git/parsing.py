"""Pure parsers for NUL-delimited git output (SPEC §8: never parse quoted paths)."""

import re
from dataclasses import dataclass

from rift.core.errors import ErrorCode
from rift.core.models.change import ChangeStatus, LineRange, merge_line_ranges
from rift.core.paths import is_repo_path
from rift.git.errors import GitError

_STATUS = {
    "A": ChangeStatus.ADDED,
    "M": ChangeStatus.MODIFIED,
    "D": ChangeStatus.DELETED,
    "R": ChangeStatus.RENAMED,
    "C": ChangeStatus.COPIED,
    "T": ChangeStatus.TYPE_CHANGED,
    # Unmerged paths (conflicts) are treated as modified: their content differs from base.
    "U": ChangeStatus.MODIFIED,
}
_FILTER_KEY_DOTS = 2  # filter.<name>.<variable>
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


_RAW_HEADER = re.compile(
    r"^:(?P<old_mode>[0-7]{6}) (?P<new_mode>[0-7]{6}) "
    r"(?P<old_sha>[0-9a-f]{40}(?:[0-9a-f]{24})?) (?P<new_sha>[0-9a-f]{40}(?:[0-9a-f]{24})?) "
    r"(?P<status>[A-Z])(?P<score>\d*)$"
)
GITLINK_MODE = "160000"


@dataclass(frozen=True, slots=True, kw_only=True)
class RawChange:
    """One ``git diff --raw`` record. A ``None`` sha means "not in the object store" (all zeros:
    the side is absent, or it is working-tree content git did not hash)."""

    status: ChangeStatus
    path: str
    old_path: str | None
    old_mode: str
    new_mode: str
    old_sha: str | None
    new_sha: str | None

    @property
    def is_gitlink(self) -> bool:
        return GITLINK_MODE in {self.old_mode, self.new_mode}


def _unexpected(message: str) -> GitError:
    return GitError(message, code=ErrorCode.UNEXPECTED_GIT_OUTPUT)


def decode_path(raw: bytes) -> str:
    """Decode a git path (UTF-8) and check it is a normalized repository-relative path.

    Non-UTF-8 names cannot be represented in canonical JSON; RIFT refuses them explicitly rather
    than guessing an encoding (ADR-0006).
    """
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitError(
            f"path is not valid UTF-8: {raw!r}",
            code=ErrorCode.UNDECODABLE_PATH,
            hint="rename the file to a UTF-8 name; RIFT cannot represent it faithfully",
        ) from exc
    if not is_repo_path(path):
        raise _unexpected(f"git reported a non-normalized path: {path!r}")
    return path


def _fields(data: bytes) -> list[bytes]:
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise _unexpected("NUL-delimited output did not end with NUL")
    return data[:-1].split(b"\0")


def _sha(value: str) -> str | None:
    return None if set(value) == {"0"} else value


def parse_raw_z(data: bytes) -> list[RawChange]:
    """Parse ``git diff --raw -z --no-abbrev`` output.

    Records are ``:<old mode> <new mode> <old sha> <new sha> <status><score>\\0<path>\\0`` with a
    second path for renames/copies (``old\\0new``).
    """
    tokens = _fields(data)
    changes: list[RawChange] = []
    index = 0
    while index < len(tokens):
        header = _RAW_HEADER.match(tokens[index].decode("ascii", errors="replace"))
        if header is None:
            raise _unexpected(f"unrecognized raw diff record {tokens[index][:80]!r}")
        letter = header["status"]
        if letter not in _STATUS:
            raise _unexpected(f"unknown diff status {letter!r}")
        paired = letter in {"R", "C"}
        if index + (2 if paired else 1) >= len(tokens):
            raise _unexpected("truncated raw diff record")
        if paired:
            old_path: str | None = decode_path(tokens[index + 1])
            path = decode_path(tokens[index + 2])
            index += 3
        else:
            old_path, path = None, decode_path(tokens[index + 1])
            index += 2
        changes.append(
            RawChange(
                status=_STATUS[letter],
                path=path,
                old_path=old_path,
                old_mode=header["old_mode"],
                new_mode=header["new_mode"],
                old_sha=_sha(header["old_sha"]),
                new_sha=_sha(header["new_sha"]),
            )
        )
    return changes


def count_lines(data: bytes) -> int:
    """Number of lines in ``data`` (a final line without a newline still counts)."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def parse_numstat_z(data: bytes) -> dict[str, bool]:
    """Parse ``git diff --numstat -z``; map each (new) path to whether git treats it as binary."""
    tokens = _fields(data)
    binary: dict[str, bool] = {}
    index = 0
    while index < len(tokens):
        added, deleted, rest = [*tokens[index].split(b"\t", 2), b"", b""][:3]
        is_binary = added == b"-" and deleted == b"-"
        if rest:
            path = decode_path(rest)
            index += 1
        else:  # rename/copy: "added\tdeleted\t\0old\0new"
            if index + 2 >= len(tokens):
                raise _unexpected("truncated numstat rename record")
            path = decode_path(tokens[index + 2])
            index += 3
        binary[path] = is_binary
    return binary


def parse_hunk_ranges(patch: bytes, *, old_side: bool) -> tuple[LineRange, ...]:
    """Line ranges from ``-U0`` hunk headers.

    New side (default): a hunk ``+c,d`` covers lines ``c..c+d-1``. A pure deletion (``d == 0``)
    has no new lines; it is recorded as the single line ``max(c, 1)`` next to the deletion, so the
    enclosing symbol still counts as changed (ADR-0005). ``old_side`` reads ``-a,b`` instead (for
    deleted files).
    """
    ranges: list[LineRange] = []
    for match in _HUNK.finditer(patch):
        if old_side:
            start, count = int(match.group(1)), int(match.group(2) or b"1")
        else:
            start, count = int(match.group(3)), int(match.group(4) or b"1")
        if count == 0:
            anchor = max(start, 1)
            ranges.append(LineRange(start=anchor, end=anchor))
        else:
            ranges.append(LineRange(start=start, end=start + count - 1))
    return merge_line_ranges(ranges)


def parse_paths_z(data: bytes) -> list[str]:
    """Parse ``git ls-files -z`` style output."""
    return [decode_path(token) for token in _fields(data)]


def parse_filter_drivers(data: bytes, scopes: frozenset[str]) -> list[str]:
    """Filter driver names from ``git config --show-scope -z --get-regexp ^filter\\.``.

    Records are ``scope\\0key\\nvalue\\0``; only drivers defined in ``scopes`` are returned.
    """
    tokens = _fields(data)
    if len(tokens) % 2:
        raise _unexpected("malformed git config --show-scope output")
    names: set[str] = set()
    for scope_raw, entry in zip(tokens[0::2], tokens[1::2], strict=True):
        if scope_raw.decode("utf-8", errors="replace") not in scopes:
            continue
        key = entry.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        if not key.startswith("filter.") or key.count(".") < _FILTER_KEY_DOTS:
            continue
        names.add(key[len("filter.") : key.rindex(".")])
    return sorted(names)
