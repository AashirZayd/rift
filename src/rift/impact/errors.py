"""Impact errors."""

from rift.core.errors import ErrorCode, RiftError


class ImpactError(RiftError):
    """Impact could not be computed for the given input."""

    default_code = ErrorCode.INVALID_ARGUMENT


class SymbolNotFoundError(ImpactError):
    """No analyzed symbol matches a ``--symbol`` query."""

    default_code = ErrorCode.SYMBOL_NOT_FOUND
