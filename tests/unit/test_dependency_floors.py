"""Runtime dependency floors (ADR-0003): the oldest allowed versions must actually work.

The lowest-direct CI job installs every direct dependency at its declared minimum and runs the whole
suite, so these tests exercise the floors, not just today's lockfile versions.
"""

import os
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
_FLOOR = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)\s*>=\s*(?P<floor>[0-9][0-9.]*)")


def _floors() -> dict[str, str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floors = {}
    for requirement in pyproject["project"]["dependencies"]:
        match = _FLOOR.match(requirement)
        assert match is not None, f"every runtime dependency needs a >= floor: {requirement}"
        floors[match["name"]] = match["floor"]
    return floors


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def test_declared_floors() -> None:
    assert _floors() == {"jsonschema": "4.0.1", "rich": "14.2.0", "typer": "0.28"}


def test_jsonschema_draft_2020_12_features_rift_relies_on() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "$defs": {"commit": {"type": ["string", "null"], "pattern": "^[0-9a-f]{40}$"}},
        "properties": {
            "base": {"$ref": "#/$defs/commit"},
            "pair": {"type": "array", "prefixItems": [{"type": "integer"}], "items": False},
        },
        "patternProperties": {"^x-[a-z0-9-]+$": True},  # SPEC §16 extension keys
        "unevaluatedProperties": False,  # unknown keys -> validation error
    }
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"base": "a" * 40, "pair": [1], "x-vendor": {"any": 1}})
    assert not validator.is_valid({"base": "nope"})  # pattern reached through $ref/$defs
    assert not validator.is_valid({"pair": [1, 2]})  # items: false after prefixItems
    assert not validator.is_valid({"pair": ["one"]})
    assert not validator.is_valid({"unknown": 1})  # unevaluatedProperties


@pytest.mark.skipif(
    os.environ.get("RIFT_EXPECT_LOWEST_DIRECT") != "1",
    reason="only meaningful in the lowest-direct CI job, which sets RIFT_EXPECT_LOWEST_DIRECT=1",
)
def test_lowest_direct_job_really_installed_the_floors() -> None:
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    for name, floor in _floors().items():
        installed = version(name)
        assert _release(installed) == _release(floor), f"{name}: {installed} != floor {floor}"
        locked = re.search(rf'name = "{name}"\nversion = "([^"]+)"', lock)
        assert locked is not None
        if name in {"rich", "jsonschema"}:  # these floors were loosened below the lockfile
            assert _release(floor) < _release(locked[1]), f"{name} floor is not older than lock"
