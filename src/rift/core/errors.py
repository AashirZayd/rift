"""Structured error hierarchy. Every RIFT error carries a stable code, a message and a hint."""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable error codes (serialized form = value)."""

    INVALID_MODEL = "invalid_model"
    SERIALIZATION = "serialization"
    NON_CANONICAL_VALUE = "non_canonical_value"
    INVALID_PATH = "invalid_path"
    INVALID_ID = "invalid_id"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_ARGUMENT = "invalid_argument"
    GIT_FAILED = "git_failed"
    GIT_TIMEOUT = "git_timeout"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_TOO_OLD = "git_too_old"
    NOT_A_REPOSITORY = "not_a_repository"
    INVALID_REVISION = "invalid_revision"
    SHALLOW_HISTORY = "shallow_history"
    UNSAFE_REPOSITORY = "unsafe_repository"
    NO_BASE_REVISION = "no_base_revision"
    UNDECODABLE_PATH = "undecodable_path"
    UNEXPECTED_GIT_OUTPUT = "unexpected_git_output"
    MISSING_OBJECTS = "missing_objects"
    UNREADABLE_FILE = "unreadable_file"
    UNKNOWN_NODE = "unknown_node"
    GRAPH_CONFLICT = "graph_conflict"
    SYMBOL_NOT_FOUND = "symbol_not_found"


class RiftError(Exception):
    """Base class for all RIFT errors.

    ``context`` holds sorted ``(key, value)`` string pairs for diagnostics; it never contains
    environment values or credentials.
    """

    default_code: ErrorCode = ErrorCode.INVALID_MODEL

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        hint: str | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code if code is not None else self.default_code
        self.message: str = message
        self.hint: str | None = hint
        self.context: tuple[tuple[str, str], ...] = tuple(sorted((context or {}).items()))

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        if self.hint:
            text += f" (hint: {self.hint})"
        return text


class ModelValidationError(RiftError, ValueError):
    """A model was constructed with values that violate its invariants."""

    default_code = ErrorCode.INVALID_MODEL


class SerializationError(RiftError, ValueError):
    """A serialized document could not be decoded into a model."""

    default_code = ErrorCode.SERIALIZATION
