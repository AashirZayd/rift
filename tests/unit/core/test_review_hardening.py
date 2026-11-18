"""Regression tests for the M1 independent review: hostile input never escapes as a raw exception,
digits are ASCII-only, local paths and secrets cannot enter proofs, user data is never stripped."""

from typing import Any

import pytest

from rift.core.canonical import NonCanonicalValueError, canonical_json
from rift.core.clock import parse_timestamp
from rift.core.errors import ModelValidationError, SerializationError
from rift.core.frozen import MAX_DEPTH, FrozenMap, freeze
from rift.core.ids import IdPrefix, content_id, strip_volatile
from rift.core.models._checks import _matches
from rift.core.models.graph import Node
from rift.core.models.proof import _SEMVER, ProofSubject, remote_url_problem
from rift.core.models.repo import RepositoryRevision
from rift.core.paths import is_repo_path
from rift.core.serialization import from_dict, to_dict

REV = RepositoryRevision(commit="a" * 40, ref=None, is_detached=False, is_dirty=False)


def _nested(depth: int) -> Any:
    value: Any = 0
    for _ in range(depth):
        value = {"k": value}
    return value


def test_deeply_nested_model_data_is_a_serialization_error() -> None:
    node = {"id": "py:a", "kind": "module", "label": "a", "location": None}
    for depth in (MAX_DEPTH + 5, 50_000):
        with pytest.raises(SerializationError):
            from_dict(Node, {**node, "attributes": _nested(depth)})
    with pytest.raises(ModelValidationError):
        freeze(_nested(MAX_DEPTH + 5))
    assert freeze(_nested(10)) is not None  # ordinary nesting is fine


def test_malformed_remote_url_is_a_serialization_error_not_a_raw_value_error() -> None:
    data = to_dict(ProofSubject(remote_url=None, base=REV, head=REV, is_dirty=False))
    data["remote_url"] = "http://[::1"
    with pytest.raises(SerializationError):
        from_dict(ProofSubject, data)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://example.invalid/org/repo.git", True),
        ("git@example.invalid:org/repo.git", True),
        ("ssh://git@example.invalid/org/repo.git", True),
        ("https://token@example.invalid/repo.git", False),
        ("https://example.invalid/repo.git?access_token=abc", False),
        ("https://example.invalid/repo.git#frag", False),
        ("file:///home/alice/repo", False),
        ("/home/alice/repo", False),
        ("C:/Users/alice/repo", False),
        ("C:\\Users\\alice\\repo", False),
        ("../relative/repo", False),
        ("http://[::1", False),
    ],
)
def test_remote_url_policy(url: str, ok: bool) -> None:
    assert (remote_url_problem(url) is None) is ok
    if not ok:
        with pytest.raises(ModelValidationError):
            ProofSubject(remote_url=url, base=REV, head=REV, is_dirty=False)


def test_canonical_json_rejects_non_dict_mappings_and_oversized_ints() -> None:
    with pytest.raises(NonCanonicalValueError, match="thaw"):
        canonical_json({"a": FrozenMap.of({"b": 1})})
    with pytest.raises(NonCanonicalValueError):
        canonical_json(10**5000)


@pytest.mark.parametrize(
    "text", ["\uff12\uff10\uff12\uff16-01-01T00:00:00Z", "2026-\u0661\u0662-01T00:00:00Z"]
)
def test_timestamps_accept_ascii_digits_only(text: str) -> None:
    with pytest.raises(ModelValidationError):
        parse_timestamp(text)


def test_schema_version_accepts_ascii_digits_only() -> None:
    assert _SEMVER.fullmatch("0.1.0")
    assert not _SEMVER.fullmatch("1\u0660.0.0")


@pytest.mark.parametrize(
    "path", ["C:/Users/x/secret.py", "C:", "c:\\x", "\\\\server\\share\\x", "D:/"]
)
def test_windows_absolute_paths_are_not_repo_paths(path: str) -> None:
    assert not is_repo_path(path)


def test_colon_inside_names_is_still_fine() -> None:
    assert is_repo_path("colon:name.py")
    assert is_repo_path("dir/C:/x.py")  # only a leading drive is absolute


def test_volatile_names_inside_user_data_are_content() -> None:
    payload = {
        "data": {"duration_ms": 5, "hostname": "x", "started_at": "t"},
        "attributes": {"generated_at": "t"},
        "duration_ms": 9,
    }
    assert strip_volatile(payload) == {
        "data": {"duration_ms": 5, "hostname": "x", "started_at": "t"},
        "attributes": {"generated_at": "t"},
    }
    a = content_id(IdPrefix.EVIDENCE, {"data": {"duration_ms": 1}})
    b = content_id(IdPrefix.EVIDENCE, {"data": {"duration_ms": 2}})
    assert a != b  # user data changes the ID


def test_fixed_length_tuple_annotations_are_checked_per_position() -> None:
    assert _matches((("a", 5),), tuple[tuple[str, int], ...])
    assert not _matches((("a", "5"),), tuple[tuple[str, int], ...])
    assert not _matches((("a", 5, 6),), tuple[tuple[str, int], ...])
    assert _matches(("x", 1), tuple[str, int])
    assert not _matches(("x",), tuple[str, int])
