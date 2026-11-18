"""Structured git errors. Raw ``CalledProcessError`` never escapes ``rift.git`` (SPEC §8)."""

from rift.core.errors import ErrorCode, RiftError


class GitError(RiftError):
    """A git command failed. ``stderr_tail`` holds the last lines of git's stderr."""

    default_code = ErrorCode.GIT_FAILED

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        hint: str | None = None,
        stderr_tail: str = "",
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message, code=code, hint=hint, context=context)
        self.stderr_tail: str = stderr_tail


class GitUnavailableError(GitError):
    """git is missing or older than the supported minimum (surfaced by ``rift doctor``)."""

    default_code = ErrorCode.GIT_UNAVAILABLE


class NotARepositoryError(GitError):
    default_code = ErrorCode.NOT_A_REPOSITORY


class InvalidRevisionError(GitError):
    default_code = ErrorCode.INVALID_REVISION


class ShallowHistoryError(GitError):
    """The needed history is not present because the clone is shallow."""

    default_code = ErrorCode.SHALLOW_HISTORY


class UnsafeRepositoryError(GitError):
    """git refused the repository (``safe.directory``). RIFT never edits git config."""

    default_code = ErrorCode.UNSAFE_REPOSITORY
