# Contributing to RIFT

RIFT is pre-alpha. Contributions are welcome, but expect the internals to move quickly.

## Setup

Requires Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) and git.

```bash
uv sync
uv run rift --help
```

## Quality gate

Every change must pass the full gate, in this order:

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
uv build
```

Fast loop while iterating: `uv run pytest -m "not slow"`.

## Rules that reviews enforce

- **Layering** (`docs/SPEC.md` §4.1) is checked by import-linter. Lower layers never import
  higher ones; the CLI talks only to `rift.services` and `rift.core`.
- **No execution of analyzed code** in passive commands. Never import the code under analysis.
- **Subprocesses use argument lists**; `shell=True` is banned. Git is called only via
  `rift.git.run_git`.
- **Deterministic output**: sorted collections, canonical JSON, injected clock,
  repository-relative POSIX paths.
- **Runtime dependencies** are limited to `typer`, `rich` and `jsonschema`. Adding one needs an ADR.
- **Tests**: integration tests use real git and real pytest. Never delete, skip or weaken a test to
  make the suite pass; known failures are `xfail(strict=True, reason=...)`.
- Every lint/type ignore is targeted and carries an inline reason.

## Decisions

Non-trivial decisions are recorded as ADRs in `docs/adr/` (copy `0000-template.md`).
Schema or protocol changes require an ADR, a CHANGELOG entry and a `docs/protocol.md` update.

## Commits

Small commits with conventional messages, for example `feat(git): …`, `test(policy): …`,
`docs(adr): …`.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
