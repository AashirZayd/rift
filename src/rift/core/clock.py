"""Injected clock and RFC 3339 timestamps (SPEC §7.5): UTC, ``Z`` suffix, second precision."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from rift.core.errors import ErrorCode, ModelValidationError

# [0-9], not \d: \d matches any Unicode digit (e.g. fullwidth digits), which would let a decoded
# timestamp re-encode differently and break the canonical fixed point.
_TIMESTAMP = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z$")


class Clock(Protocol):
    """Source of the current time. Pass one explicitly; never read the wall clock directly."""

    def now(self) -> datetime:
        """Return the current time as an aware UTC datetime with microsecond == 0."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The real clock, truncated to whole seconds."""

    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class FixedClock:
    """A clock that always returns the same instant (tests, reproducible runs)."""

    at: datetime

    def __post_init__(self) -> None:
        check_timestamp(self.at, "FixedClock.at")

    def now(self) -> datetime:
        return self.at


def check_timestamp(value: datetime, field: str) -> None:
    """Raise unless ``value`` is an aware UTC datetime with whole-second precision."""
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ModelValidationError(
            f"{field} must be timezone-aware UTC", code=ErrorCode.INVALID_TIMESTAMP
        )
    if value.microsecond != 0:
        raise ModelValidationError(
            f"{field} must have second precision", code=ErrorCode.INVALID_TIMESTAMP
        )


def format_timestamp(value: datetime) -> str:
    """Format as RFC 3339 ``YYYY-MM-DDTHH:MM:SSZ``."""
    check_timestamp(value, "timestamp")
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
        f"T{value.hour:02d}:{value.minute:02d}:{value.second:02d}Z"
    )


def parse_timestamp(text: str) -> datetime:
    """Parse the exact format produced by :func:`format_timestamp`."""
    match = _TIMESTAMP.fullmatch(text)
    if match is None:
        raise ModelValidationError(
            f"not an RFC 3339 UTC second-precision timestamp: {text!r}",
            code=ErrorCode.INVALID_TIMESTAMP,
        )
    try:
        year, month, day, hour, minute, second = (int(group) for group in match.groups())
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError as exc:
        raise ModelValidationError(
            f"invalid timestamp {text!r}: {exc}", code=ErrorCode.INVALID_TIMESTAMP
        ) from exc
