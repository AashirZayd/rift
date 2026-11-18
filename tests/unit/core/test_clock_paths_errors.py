"""Clock and timestamps (SPEC §7.5), repo-relative paths, and the error hierarchy."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rift.core.clock import (
    Clock,
    FixedClock,
    SystemClock,
    check_timestamp,
    format_timestamp,
    parse_timestamp,
)
from rift.core.errors import (
    ErrorCode,
    ModelValidationError,
    RiftError,
    SerializationError,
)
from rift.core.paths import is_repo_path, validate_repo_path

T0 = datetime(2026, 11, 18, 9, 5, 7, tzinfo=UTC)


def test_timestamp_roundtrip_and_format() -> None:
    assert format_timestamp(T0) == "2026-11-18T09:05:07Z"
    assert parse_timestamp("2026-11-18T09:05:07Z") == T0
    assert format_timestamp(datetime(5, 1, 2, tzinfo=UTC)) == "0005-01-02T00:00:00Z"


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),  # noqa: DTZ001  # naive on purpose
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        datetime(2026, 1, 1, microsecond=1, tzinfo=UTC),
    ],
    ids=["naive", "non-utc", "sub-second"],
)
def test_invalid_timestamps_rejected(value: datetime) -> None:
    with pytest.raises(ModelValidationError) as exc_info:
        check_timestamp(value, "t")
    assert exc_info.value.code is ErrorCode.INVALID_TIMESTAMP


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-18T09:05:07",
        "2026-11-18T09:05:07+00:00",
        "2026-11-18T09:05:07.5Z",
        "2026-02-30T00:00:00Z",
        "2026-11-18 09:05:07Z",
        "",
    ],
)
def test_parse_rejects_non_canonical_text(text: str) -> None:
    with pytest.raises(ModelValidationError):
        parse_timestamp(text)


def test_clocks() -> None:
    fixed: Clock = FixedClock(at=T0)
    assert fixed.now() == T0
    now = SystemClock().now()
    check_timestamp(now, "now")  # aware UTC, whole seconds
    with pytest.raises(ModelValidationError):
        FixedClock(at=datetime(2026, 1, 1))  # noqa: DTZ001  # naive on purpose


@pytest.mark.parametrize(
    "path",
    ["a.py", "src/pkg/a.py", "-leading-dash.py", "with space/ünï.py", "new\nline", "back\\slash"],
)
def test_valid_repo_paths(path: str) -> None:
    assert is_repo_path(path)
    assert validate_repo_path(path) == path


@pytest.mark.parametrize(
    "path", ["", "/abs", "a//b", "a/", "./a", "a/./b", "../a", "a/..", "nul\x00byte"]
)
def test_invalid_repo_paths(path: str) -> None:
    assert not is_repo_path(path)
    with pytest.raises(ModelValidationError) as exc_info:
        validate_repo_path(path)
    assert exc_info.value.code is ErrorCode.INVALID_PATH


def test_rift_error_carries_code_hint_and_sorted_context() -> None:
    err = RiftError("boom", code=ErrorCode.GIT_FAILED, hint="try x", context={"b": "2", "a": "1"})
    assert err.code is ErrorCode.GIT_FAILED
    assert err.message == "boom"
    assert err.hint == "try x"
    assert err.context == (("a", "1"), ("b", "2"))
    assert str(err) == "[git_failed] boom (hint: try x)"


def test_error_subclasses_default_codes_and_are_value_errors() -> None:
    assert ModelValidationError("x").code is ErrorCode.INVALID_MODEL
    assert SerializationError("x").code is ErrorCode.SERIALIZATION
    assert isinstance(SerializationError("x"), ValueError)
    assert str(RiftError("plain")) == "[invalid_model] plain"
