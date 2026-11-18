"""Identifiers: the node ID grammar (SPEC §6.3) and content-derived IDs (SPEC §7.3, §7.4)."""

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import NewType

from rift.core.canonical import JsonValue, canonical_bytes, sha256_hex
from rift.core.errors import ErrorCode, ModelValidationError
from rift.core.paths import is_repo_path, validate_repo_path

NodeId = NewType("NodeId", str)
SymbolId = NewType("SymbolId", str)
EvidenceId = NewType("EvidenceId", str)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Qualname segments: identifiers plus the CPython markers <locals>, <lambda>, <genexpr>, ...
_QUALNAME_SEGMENT = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|<[a-z]+>)$")


class IdPrefix(StrEnum):
    """Prefixes for content-derived identifiers (SPEC §7.3)."""

    EVIDENCE = "ev"
    CLAIM = "cl"
    PLAN = "pl"
    PROOF = "rp"


CONTENT_ID_HEX_LENGTH = 24
_CONTENT_ID = re.compile(r"^(ev|cl|pl|rp)_[0-9a-f]{24}$")

# SPEC §7.4. Excluded from IDs and from equivalence comparison.
VOLATILE_FIELDS = frozenset(
    {"generated_at", "collected_at", "started_at", "finished_at", "duration_ms"}
)
_VOLATILE_ENVIRONMENT_FIELDS = frozenset({"hostname"})
# FrozenMap model fields holding user/producer data; their contents are never stripped.
OPAQUE_FIELDS = frozenset({"data", "attributes"})


def _bad_id(kind: str, value: str) -> ModelValidationError:
    return ModelValidationError(f"invalid {kind}: {value!r}", code=ErrorCode.INVALID_ID)


def is_dotted_module(name: str) -> bool:
    """True for ``pkg.sub.mod`` where every segment is a Python identifier."""
    return bool(name) and all(_IDENTIFIER.match(part) for part in name.split("."))


def is_qualname(name: str) -> bool:
    """True for ``Class.method`` / ``func.<locals>.inner`` style qualified names."""
    return bool(name) and all(_QUALNAME_SEGMENT.match(part) for part in name.split("."))


def file_node_id(path: str) -> NodeId:
    """``file:<repo-relative-posix-path>``."""
    return NodeId(f"file:{validate_repo_path(path)}")


def module_node_id(module: str) -> NodeId:
    """``py:<dotted.module>``."""
    if not is_dotted_module(module):
        raise _bad_id("module name", module)
    return NodeId(f"py:{module}")


def symbol_node_id(module: str, qualname: str) -> NodeId:
    """``py:<dotted.module>:<qualname>``."""
    if not is_dotted_module(module):
        raise _bad_id("module name", module)
    if not is_qualname(qualname):
        raise _bad_id("qualname", qualname)
    return NodeId(f"py:{module}:{qualname}")


def node_id_for_test(pytest_node_id: str) -> NodeId:
    """``test:<pytest-node-id>``; the part before the first ``::`` must be a repo path.

    (Not named ``test_*``: pytest collects any function starting with "test" when imported.)
    """
    path, _, _ = pytest_node_id.partition("::")
    if not is_repo_path(path):
        raise _bad_id("pytest node id", pytest_node_id)
    return NodeId(f"test:{pytest_node_id}")


def validate_node_id(value: str) -> NodeId:
    """Validate a node ID against the SPEC §6.3 grammar and return it typed."""
    if not isinstance(value, str):
        raise _bad_id("node id", repr(value))
    kind, sep, rest = value.partition(":")
    if not sep:
        raise _bad_id("node id", value)
    if kind == "file" and is_repo_path(rest):
        return NodeId(value)
    if kind == "py":
        module, has_qualname, qualname = rest.partition(":")
        if is_dotted_module(module) and (not has_qualname or is_qualname(qualname)):
            return NodeId(value)
    if kind == "test":
        path, _, _ = rest.partition("::")
        if is_repo_path(path):
            return NodeId(value)
    raise _bad_id("node id", value)


def validate_symbol_id(value: str) -> SymbolId:
    """A symbol ID is a ``py:`` node ID (module or module:qualname)."""
    if not value.startswith("py:"):
        raise _bad_id("symbol id", value)
    return SymbolId(validate_node_id(value))


def validate_content_id(value: str, prefix: IdPrefix | None = None) -> str:
    """Validate ``<prefix>_<24 hex>``; optionally require a specific prefix."""
    if not isinstance(value, str) or not _CONTENT_ID.match(value):
        raise _bad_id("content id", repr(value))
    if prefix is not None and not value.startswith(f"{prefix}_"):
        raise _bad_id(f"{prefix} id", value)
    return value


def validate_evidence_id(value: str) -> EvidenceId:
    return EvidenceId(validate_content_id(value, IdPrefix.EVIDENCE))


def strip_volatile(payload: JsonValue, *, parent_key: str | None = None) -> JsonValue:
    """Return a copy of ``payload`` with SPEC §7.4 volatile fields removed at every model depth.

    ``hostname`` is volatile only directly inside an ``environment`` object. Values of the opaque
    user-data fields (``data``, ``attributes``) are kept verbatim: a key that happens to be named
    ``duration_ms`` inside evidence data is content, not a volatile model field.
    """
    if isinstance(payload, dict):
        out: dict[str, JsonValue] = {}
        for key, value in payload.items():
            if key in VOLATILE_FIELDS:
                continue
            if parent_key == "environment" and key in _VOLATILE_ENVIRONMENT_FIELDS:
                continue
            out[key] = value if key in OPAQUE_FIELDS else strip_volatile(value, parent_key=key)
        return out
    if isinstance(payload, list):
        return [strip_volatile(item, parent_key=parent_key) for item in payload]
    return payload


def content_id(prefix: IdPrefix, payload: Mapping[str, JsonValue]) -> str:
    """``<prefix>_<first 24 hex of sha256(canonical_json(payload))>`` (SPEC §7.3).

    Callers pass the payload with the ID field itself removed; volatile fields are stripped here.
    """
    stripped = strip_volatile(dict(payload))
    return f"{prefix}_{sha256_hex(canonical_bytes(stripped))[:CONTENT_ID_HEX_LENGTH]}"
