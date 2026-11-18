"""Recognise syntax that is newer than the running interpreter (SPEC §9.1, ADR-0007).

The interpreter's own parser cannot tell "invalid" from "valid in a later Python", so when parsing
fails, the failing line is matched against a small table of later grammar features. A match is
reported as ``syntax_newer_than_interpreter``; anything else stays a plain ``syntax_error``. This
is a heuristic over the failing line only and is documented as such.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FutureSyntax:
    since: tuple[int, int]
    feature: str
    pattern: re.Pattern[str]


FUTURE_SYNTAX: tuple[FutureSyntax, ...] = (
    FutureSyntax(
        (3, 12),
        "PEP 695 type alias statement",
        re.compile(r"^\s*type\s+[A-Za-z_]\w*\s*[\[=]"),
    ),
    FutureSyntax(
        (3, 12),
        "PEP 695 type parameter list",
        re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+[A-Za-z_]\w*\s*\["),
    ),
    FutureSyntax(
        (3, 14),
        "PEP 758 unparenthesized except",
        re.compile(r"^\s*except\*?\s+[^\s(:][^:]*,[^:]*:"),
    ),
    FutureSyntax(
        (3, 14),
        "PEP 750 template string",
        # Not after a word character, quote or backslash (``"a\t"`` is an escape).
        re.compile(r"(?<![\w'\"\\])(?:[tT][rR]?|[rR][tT])['\"]"),
    ),
)


def newer_syntax(line: str, running: tuple[int, int]) -> FutureSyntax | None:
    """The first table entry newer than ``running`` whose pattern matches ``line``."""
    for entry in FUTURE_SYNTAX:
        if entry.since > running and entry.pattern.search(line):
            return entry
    return None
