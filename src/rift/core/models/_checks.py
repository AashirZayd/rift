"""Shared invariant checks for model ``__post_init__`` methods (internal)."""

from collections.abc import Sequence
from dataclasses import fields
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin

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


def _matches(value: object, annotation: object) -> bool:  # noqa: PLR0911  # flat type-dispatch reads best
    """Shallow runtime check of ``value`` against a field annotation."""
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, arg) for arg in get_args(annotation))
    if annotation is type(None):
        return value is None
    if origin in (tuple, frozenset):
        if not isinstance(value, origin):
            return False
        args = get_args(annotation)
        element = args[0] if args else object
        return all(_matches(item, element) for item in cast("tuple[object, ...]", value))
    supertype = getattr(annotation, "__supertype__", None)  # typing.NewType
    if supertype is not None:
        return _matches(value, supertype)
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(annotation, type):
        return isinstance(value, annotation)
    return True  # type aliases such as FrozenValue are validated by their own types


def check_types(model: object) -> None:
    """Reject wrongly typed fields (e.g. a plain str where an enum is required, a list for a
    tuple, a bool for an int) at construction, so bad values never reach serialization."""
    for spec in fields(cast("Any", model)):
        value = getattr(model, spec.name)
        require(
            _matches(value, spec.type),
            f"{type(model).__name__}.{spec.name}: {type(value).__name__} does not match "
            f"{getattr(spec.type, '__name__', spec.type)}",
        )
