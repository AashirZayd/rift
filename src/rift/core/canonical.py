"""Canonical JSON (SPEC §7.1) and content hashing.

Canonical form: UTF-8, keys sorted, ``(",", ":")`` separators, non-ASCII kept literally, no
NaN/Infinity. Floats are rejected outright (ADR-0004): counts and durations are integers, and
confidence is an ordinal enum, so a float in a RIFT document is always a bug.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from rift.core.errors import ErrorCode, RiftError

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = "JsonScalar | list[JsonValue] | dict[str, JsonValue]"


class NonCanonicalValueError(RiftError, TypeError):
    """A value cannot be represented in canonical JSON."""

    default_code = ErrorCode.NON_CANONICAL_VALUE


def _check(value: object, where: str) -> None:
    """Reject anything canonical JSON does not allow. Iterative to survive deep nesting."""
    stack: list[tuple[object, str]] = [(value, where)]
    while stack:
        current, path = stack.pop()
        if current is None or isinstance(current, str | bool | int):
            if isinstance(current, str):
                try:
                    current.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise NonCanonicalValueError(
                        f"string at {path} is not valid Unicode (lone surrogate)"
                    ) from exc
            continue
        if isinstance(current, float):
            raise NonCanonicalValueError(f"float at {path}: canonical JSON allows integers only")
        if isinstance(current, Mapping) and not isinstance(current, dict):
            # json.dumps only serializes real dicts; anything else (e.g. FrozenMap) must be thawed.
            raise NonCanonicalValueError(
                f"non-dict mapping {type(current).__name__} at {path}; thaw it first"
            )
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise NonCanonicalValueError(f"non-string key {key!r} at {path}")
                stack.append((item, f"{path}.{key}"))
            continue
        if isinstance(current, list | tuple):
            seq: Sequence[object] = current
            stack.extend((item, f"{path}[{i}]") for i, item in enumerate(seq))
            continue
        raise NonCanonicalValueError(f"unsupported type {type(current).__name__} at {path}")


def canonical_json(value: object) -> str:
    """Serialize ``value`` to canonical JSON text."""
    _check(value, "$")
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except RecursionError as exc:  # pathologically deep nesting (SPEC §21 threat table)
        raise NonCanonicalValueError("value is nested too deeply to serialize") from exc
    except ValueError as exc:  # e.g. an int beyond sys.get_int_max_str_digits()
        raise NonCanonicalValueError(f"value cannot be serialized: {exc}") from exc


def canonical_bytes(value: object) -> bytes:
    """Canonical JSON encoded as UTF-8 (the form that is hashed)."""
    return canonical_json(value).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()
