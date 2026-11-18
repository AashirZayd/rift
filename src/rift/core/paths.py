"""Repository-relative POSIX paths (SPEC §5, §7.7): the only path form allowed in artifacts."""

from rift.core.errors import ErrorCode, ModelValidationError


def is_repo_path(path: str) -> bool:
    """True if ``path`` is a normalized repository-relative POSIX path.

    Rejected: empty, absolute (leading ``/``), empty segments (``a//b``, trailing ``/``),
    ``.``/``..`` segments, and NUL. Everything else git can store is accepted, including spaces,
    newlines, backslashes, unicode and a leading ``-``: those are legal file names, and refusing
    them would make RIFT fail on real repositories.
    """
    if not path or path.startswith("/") or "\x00" in path:
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
