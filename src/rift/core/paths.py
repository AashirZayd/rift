"""Repository-relative POSIX paths (SPEC §5, §7.7): the only path form allowed in artifacts."""

import re

from rift.core.errors import ErrorCode, ModelValidationError

# A Windows drive prefix ("C:", "C:/x", "C:\x") or UNC-style backslash root: absolute on Windows.
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:(?:[\\/]|$)|\\\\)")


def is_repo_path(path: str) -> bool:
    """True if ``path`` is a normalized repository-relative POSIX path.

    Rejected: empty, absolute (leading ``/``, a Windows drive such as ``C:/`` or ``C:``, or a
    ``\\\\`` UNC prefix), empty segments (``a//b``, trailing ``/``), ``.``/``..`` segments, and NUL.
    Everything else git can store is accepted, including spaces, newlines, backslashes, unicode and
    a leading ``-``: those are legal file names, and refusing them would make RIFT fail on real
    repositories. Trade-off (ADR-0005): a top-level directory literally named ``C:`` cannot be
    represented.
    """
    if not path or path.startswith("/") or "\x00" in path or _WINDOWS_ABSOLUTE.match(path):
        return False
    return all(segment not in {"", ".", ".."} for segment in path.split("/"))


def validate_repo_path(path: str, field: str = "path") -> str:
    """Return ``path`` unchanged if valid, else raise :class:`ModelValidationError`."""
    if not isinstance(path, str) or not is_repo_path(path):
        raise ModelValidationError(
            f"{field} must be a repository-relative POSIX path, got {path!r}",
            code=ErrorCode.INVALID_PATH,
        )
    return path
