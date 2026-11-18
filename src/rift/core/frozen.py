"""Immutable JSON values for use inside frozen models.

``FrozenMap`` is the "small frozen mapping type in core" from SPEC §5: a hashable, immutable,
key-sorted mapping whose values are frozen JSON values (scalars, tuples, nested FrozenMaps).
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TypeAlias

from rift.core.canonical import JsonValue
from rift.core.errors import ModelValidationError

FrozenScalar: TypeAlias = str | int | bool | None
FrozenValue: TypeAlias = "FrozenScalar | tuple[FrozenValue, ...] | FrozenMap"


@dataclass(frozen=True, slots=True)
class FrozenMap(Mapping[str, "FrozenValue"]):
    """Immutable mapping with string keys, stored sorted by key."""

    entries: tuple[tuple[str, FrozenValue], ...] = ()

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.entries]
        if any(not isinstance(key, str) for key in keys):
            raise ModelValidationError("FrozenMap keys must be strings")
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ModelValidationError("FrozenMap items must be unique and sorted by key")
        for _, value in self.entries:
            _check_frozen(value)

    @classmethod
    def of(cls, mapping: Mapping[str, FrozenValue] | None = None) -> "FrozenMap":
        """Build from any mapping of already-frozen values."""
        return cls(tuple(sorted((mapping or {}).items())))

    def __getitem__(self, key: str) -> FrozenValue:
        for item_key, value in self.entries:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def _check_frozen(value: object) -> None:
    if value is None or isinstance(value, str | bool | int | FrozenMap):
        return
    if isinstance(value, tuple):
        for item in value:
            _check_frozen(item)
        return
    raise ModelValidationError(f"not a frozen JSON value: {type(value).__name__}")


def freeze(value: JsonValue) -> FrozenValue:
    """Convert a plain JSON value (dict/list) into its frozen form."""
    if isinstance(value, dict):
        return FrozenMap.of({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, float):
        raise ModelValidationError("floats are not allowed in RIFT data")
    return value


def thaw(value: FrozenValue) -> JsonValue:
    """Convert a frozen value back into plain JSON (dict/list)."""
    if isinstance(value, FrozenMap):
        return {key: thaw(item) for key, item in value.entries}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value
