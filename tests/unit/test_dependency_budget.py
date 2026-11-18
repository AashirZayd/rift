"""SPEC §3.1 runtime dependency budget, checked against the installed distribution metadata."""

import re
from importlib.metadata import PackageNotFoundError, requires

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_requirements(dist: str) -> frozenset[str]:
    """Names of non-extra requirements of an installed distribution (markers otherwise ignored)."""
    names: set[str] = set()
    for req in requires(dist) or ():
        if re.search(r"\bextra\s*==", req):
            continue
        match = _NAME.match(req)
        assert match is not None, req
        names.add(_normalize(match.group(0)))
    return frozenset(names)


def _closure(dist: str) -> frozenset[str]:
    seen: set[str] = set()
    stack = [dist]
    while stack:
        current = stack.pop()
        try:
            deps = _runtime_requirements(current)
        except PackageNotFoundError:
            # Not installed here, e.g. a platform-marker dependency for another OS.
            continue
        for dep in sorted(deps - seen):
            seen.add(dep)
            stack.append(dep)
    return frozenset(seen)


def test_direct_runtime_dependencies_are_the_budget() -> None:
    assert _runtime_requirements("rift-cli") == {"typer", "rich", "jsonschema"}


def test_no_pydantic_anywhere_in_the_runtime_closure() -> None:
    # SPEC §3.1 / ADR-0003: pydantic is not used, not even transitively (typer is capped < 0.29).
    closure = _closure("rift-cli")
    assert "typer" in closure  # guard: the walk actually ran
    assert not {"pydantic", "pydantic-core"} & closure
