# Project Status
_Last updated: 2026-11-18 — by: Claude Code session (M0 build)_

## Current milestone
M0 — Foundation — in progress (gate green; independent review running)

## Completed (verified)
- `uv sync` from lockfile; `uv.lock` committed — verified by `uv sync --locked` (exit 0)
- `rift --help`, `rift version`, `rift version --json` — verified by `tests/contract/test_cli.py` (in-process + real subprocess; stdout is exactly one JSON document, stderr empty)
- `python -m rift` — verified by `tests/contract/test_cli.py::test_python_dash_m_entry_point`
- Version single-sourced in `src/rift/__init__.py` — verified by `tests/unit/test_version.py::test_version_is_single_sourced`
- `uv build` produces sdist + wheel — verified by `uv build` (`rift_cli-0.0.0.tar.gz`, `rift_cli-0.0.0-py3-none-any.whl`)
- Wheel installs in a fresh env and runs — verified by `uv run --isolated --no-project --python 3.11 --with dist/rift_cli-0.0.0-py3-none-any.whl rift version` → `rift 0.0.0` (also 3.13, `--json`)
- Every §4 subpackage exists, imports, has a responsibility docstring, holds no logic — verified by `tests/unit/test_packages.py`
- Layering (§4.1) enforced — verified by `uv run lint-imports` (5 kept, 0 broken) and `tests/contract/test_layering.py` (planted violations are rejected)
- Ruff, mypy strict, pytest configured and green — verified by the §23 gate (62 passed, coverage 97% branch)

## Pending
- Independent review of the M0 diff, then fix any findings and re-run the gate
- Completion report

## Known limitations
- CI workflow has not run on GitHub (never pushed) — green status unverified.
- `uv` on the dev machine is not on PATH (`%APPDATA%\Python\Python314\Scripts`); commands prepend it.
- typer is capped `<0.29` to avoid its new transitive pydantic dependency (ADR-0003).
- SECURITY.md and CODE_OF_CONDUCT.md contain contact placeholders to be filled before the repo is public.

## Failing / xfail tests
- None.

## Architectural decisions (ADR index)
- [ADR-0001](docs/adr/0001-record-architecture-decisions.md) — Record architecture decisions
- [ADR-0002](docs/adr/0002-modular-monolith-and-layering.md) — Modular monolith with enforced layering; entry point `rift.cli.app:app`
- [ADR-0003](docs/adr/0003-dependencies-build-backend-and-name.md) — Dependency budget (typer <0.29), hatchling, schemas in `rift.core.schemas` + `specs/` mirror, name `rift-cli`

## Next tasks
- Finish M0 review/report; await "M0 accepted".
- M1 — Core models & Git (not started; requires approval).
