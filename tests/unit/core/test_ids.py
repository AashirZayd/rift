"""Node ID grammar (SPEC §6.3), content IDs and volatile fields (SPEC §7.3, §7.4)."""

import re

import pytest

from rift.core.canonical import JsonValue
from rift.core.errors import ErrorCode, ModelValidationError
from rift.core.ids import (
    IdPrefix,
    content_id,
    file_node_id,
    module_node_id,
    node_id_for_test,
    strip_volatile,
    symbol_node_id,
    validate_content_id,
    validate_node_id,
    validate_symbol_id,
)


def test_constructors_follow_the_grammar() -> None:
    assert file_node_id("src/pkg/auth.py") == "file:src/pkg/auth.py"
    assert module_node_id("pkg.auth") == "py:pkg.auth"
    assert symbol_node_id("pkg.auth", "Session.refresh") == "py:pkg.auth:Session.refresh"
    assert symbol_node_id("m", "f.<locals>.g") == "py:m:f.<locals>.g"
    assert (
        node_id_for_test("tests/test_auth.py::TestLogin::test_ok")
        == "test:tests/test_auth.py::TestLogin::test_ok"
    )


@pytest.mark.parametrize(
    "value",
    [
        "file:src/a.py",
        "file:dir with space/-weird\nname.py",
        "py:pkg",
        "py:pkg.mod:Cls.meth",
        "test:tests/t.py::test_x[param-1]",
    ],
)
def test_valid_node_ids(value: str) -> None:
    assert validate_node_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "src/a.py",
        "file:",
        "file:/abs/path.py",
        "file:a/../b.py",
        "file:a//b.py",
        "py:",
        "py:1bad",
        "py:pkg..mod",
        "py:pkg:",
        "py:pkg:Cls meth",
        "test:",
        "test:/abs::t",
        "api:something",
    ],
)
def test_invalid_node_ids(value: str) -> None:
    with pytest.raises(ModelValidationError) as exc_info:
        validate_node_id(value)
    assert exc_info.value.code is ErrorCode.INVALID_ID


def test_symbol_ids_are_py_node_ids() -> None:
    assert validate_symbol_id("py:a.b:C") == "py:a.b:C"
    with pytest.raises(ModelValidationError):
        validate_symbol_id("file:a.py")


def test_content_id_format_and_prefix_checks() -> None:
    value = content_id(IdPrefix.EVIDENCE, {"a": 1})
    assert re.fullmatch(r"ev_[0-9a-f]{24}", value)
    assert validate_content_id(value, IdPrefix.EVIDENCE) == value
    with pytest.raises(ModelValidationError):
        validate_content_id(value, IdPrefix.CLAIM)
    for bad in ["ev_123", "xx_" + "0" * 24, "ev_" + "G" * 24, "EV_" + "0" * 24]:
        with pytest.raises(ModelValidationError):
            validate_content_id(bad)


def test_content_id_is_sha256_of_canonical_payload() -> None:
    # sha256('{"a":1}') = 015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862
    assert content_id(IdPrefix.PLAN, {"a": 1}) == "pl_015abd7f5cc57a2dd94b7590"


def test_volatile_fields_are_ignored_by_ids() -> None:
    base: dict[str, JsonValue] = {
        "subject": "x",
        "run": {"started_at": "2026-01-01T00:00:00Z", "exit_code": 0},
    }
    later: dict[str, JsonValue] = {
        "subject": "x",
        "run": {"started_at": "2030-01-01T00:00:00Z", "exit_code": 0},
    }
    assert content_id(IdPrefix.EVIDENCE, base) == content_id(IdPrefix.EVIDENCE, later)
    changed: dict[str, JsonValue] = {
        "subject": "y",
        "run": {"started_at": "2026-01-01T00:00:00Z", "exit_code": 0},
    }
    assert content_id(IdPrefix.EVIDENCE, base) != content_id(IdPrefix.EVIDENCE, changed)


def test_strip_volatile_depth_and_hostname_scoping() -> None:
    payload: dict[str, JsonValue] = {
        "generated_at": "t",
        "environment": {"hostname": "h", "platform": "linux"},
        "items": [{"collected_at": "t", "duration_ms": 5, "keep": 1}],
        "other": {"hostname": "kept-outside-environment"},
        "finished_at": "t",
    }
    assert strip_volatile(payload) == {
        "environment": {"platform": "linux"},
        "items": [{"keep": 1}],
        "other": {"hostname": "kept-outside-environment"},
    }
