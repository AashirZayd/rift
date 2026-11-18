# Project Status
_Last updated: 2026-11-18 — by: Claude Code session (M0 build)_

## Current milestone
M0 — Foundation — awaiting review

## Completed (verified)
- `uv sync` from a clean checkout; `uv.lock` committed — verified by `uv sync --locked` in a fresh `git clone` (exit 0; 76 tests pass there)
- `rift --help`, `rift version`, `rift version --json` (the only CLI surface) — verified by `tests/contract/test_cli.py` (in-process + real subprocess; `--json` stdout is exactly one canonical JSON document, stderr empty; command set == `["version"]`)
- `python -m rift` — verified by `tests/contract/test_cli.py::test_python_dash_m_entry_point`
- Version single-sourced in `src/rift/__init__.py` (pyproject declares it dynamic, hatchling reads the file) — verified by `tests/unit/test_version.py::test_version_is_single_sourced`
- Runtime dependency budget (§3.1): direct deps == {typer, rich, jsonschema}; no pydantic in the closure — verified by `tests/unit/test_dependency_budget.py`
- `uv build` produces sdist + wheel — verified by `uv build` (`rift_cli-0.0.0.tar.gz`, `rift_cli-0.0.0-py3-none-any.whl`)
- Wheel installs in a fresh env and prints the version — verified by the CI smoke script run locally (`human: rift 0.0.0`, `json: {"name":"rift","version":"0.0.0"}`, exit 0; fails with exit 1 on mismatch); also run on Python 3.11 and 3.13
- Every §4 subpackage exists, imports, has a responsibility docstring, holds no logic — verified by `tests/unit/test_packages.py`
- Layering (§4.1) encoded in 8 import-linter contracts: top-level layers (exhaustive), no cycles anywhere, core/plugins.api/proof/cli forbidden rules, plugins-internal layering, renderers only in cli — verified by `uv run lint-imports` (8 kept, 0 broken), by planted-violation tests in `tests/contract/test_layering.py` (14 cases, each must report the expected contract BROKEN), and by drift tests tying forbidden lists to the real subpackage set
- Ruff, mypy strict, pytest configured and green — verified by the §23 gate: format 53 files clean, ruff clean, mypy 0 issues in 36 files, 76 passed, branch coverage 97%

## Pending
- User review of M0.

## Known limitations
- CI workflow has never run on GitHub (not pushed). It triggers on push to `main`, `pull_request`, and `workflow_dispatch` — pushing only `m0/foundation` will not run it; open a PR or dispatch manually.
- typer is capped `>=0.28,<0.29` to avoid its new transitive pydantic dependency (ADR-0003). Revisit if a typer fix lands only in ≥ 0.29.
- Runtime lower bounds (`rich>=15.0.0`, `jsonschema>=4.26.0`) are the versions resolved today; older versions are untested, so the bounds are not loosened.
- Root-package import from `rift.core` cannot be expressed in import-linter; covered by an AST test instead (ADR-0002).
- SECURITY.md and CODE_OF_CONDUCT.md contain contact placeholders to fill before the repo is public.

## Failing / xfail tests
- None.

## Architectural decisions (ADR index)
- [ADR-0001](docs/adr/0001-record-architecture-decisions.md) — Record architecture decisions
- [ADR-0002](docs/adr/0002-modular-monolith-and-layering.md) — Modular monolith, enforced layering (8 contracts), entry point `rift.cli.app:app`
- [ADR-0003](docs/adr/0003-dependencies-build-backend-and-name.md) — typer <0.29 (no pydantic), hatchling, schemas + loader in `rift.core.schemas` with `specs/` mirror (amends §4), name `rift-cli`, JUnit XML parsing note

## Next tasks
- Await "M0 accepted".
- M1 — Core models & Git (§6 models + serialization round-trip properties, canonical JSON + IDs, Clock, error hierarchy, full §8 git layer with real-git integration tests). Not started.
