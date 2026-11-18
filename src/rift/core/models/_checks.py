"""Shared invariant checks for model ``__post_init__`` methods (internal)."""

from collections.abc import Sequence

from rift.core.errors import ModelValidationError


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelValidationError(message)


def non_empty(value: str, field: str) -> None:
    require(isinstance(value, str) and value != "", f"{field} must be a non-empty string")


def non_negative(value: int, field: str) -> None:
    require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{field} must be a non-negative integer",
    )


def sorted_unique(values: Sequence[str], field: str) -> None:
    """Determinism (SPEC §7.2): ID collections are stored sorted and without duplicates."""
    require(list(values) == sorted(set(values)), f"{field} must be sorted and free of duplicates")


def is_hex(value: str, lengths: tuple[int, ...]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(char in "0123456789abcdef" for char in value)
    )
