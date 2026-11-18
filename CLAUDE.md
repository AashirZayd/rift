# CLAUDE.md — RIFT

RIFT: deterministic change-impact + verification engine that emits schema-validated **Change Proofs**. Python ≥3.11, uv, Typer/Rich, stdlib `ast` + `sqlite3`, JSON Schema. MIT.

- **What to build:** `docs/SPEC.md` (source of truth — read the relevant section before touching a subsystem).
- **Where we are:** `PROJECT_STATUS.md` (read first every session; update before ending).
- **Why things are the way they are:** `docs/adr/`.

## Commands

```bash
uv sync                       # install (use --locked in CI)
uv run rift --help
uv run pytest                 # full suite
uv run pytest -m "not slow"   # fast loop
uv run pytest tests/unit/path/test_x.py::test_name
uv run ruff format .          # format
uv run ruff check . --fix     # lint
uv run mypy                   # strict on src/
uv run lint-imports           # layering contract
uv build                      # sdist + wheel
```

**Quality gate** (run all, in order, before claiming anything is done — `/verify` does this):
`uv sync --locked && uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest && uv build`

## Architecture — layers (lower never imports higher; enforced by import-linter)

```
cli → services → proof → policy → verification → impact|invariants → analysis
    → graph|evidence|cache|git|discovery|config|plugins → core
```
- `core`: models, enums, IDs, errors, canonical JSON, clock. Imports nothing from `rift`.
- `cli` imports only `services` + `core`. Commands are thin: parse → call one service → render.
- `proof` never imports analysis/graph/impact/verification/git. It takes finished values.
- `plugins.api` is the **only** public API. Imports only `core`.

## Hard rules

- **No repo code execution in passive commands.** Never import analyzed code. `pytest --collect-only` counts as execution (it loads `conftest.py`) — only behind `--execute`.
- **Subprocess = argv list.** Never `shell=True`. Git calls only via `rift.git.run_git` (hardening args, `-z`, `--end-of-options`, `--` before paths).
- **Determinism:** sort every output collection; never iterate a `set` into output; canonical JSON for hashing; inject `Clock`; repo-relative POSIX paths only; no env values / absolute paths / credentials in proofs.
- **Conservative verdicts:** violated mandatory → FAIL; missing/unsupported mandatory → INCONCLUSIVE; else PASS. Unknown is never success.
- **Provenance:** every edge has `origin` + `confidence` (enum, not float). Never present inferred as confirmed, or selected as executed.
- Models: `@dataclass(frozen=True, slots=True, kw_only=True)`, tuples not lists, `StrEnum`. No untyped dicts across module boundaries.
- Errors: subclass `RiftError` with `code`/`hint`. No bare `except`, no swallowed exceptions. Broad catches only at plugin boundary + CLI top level.
- No `print` outside `cli`. No global mutable state. No `time.sleep`. No network.
- Runtime deps are capped at `typer`, `rich`, `jsonschema`. Anything else needs an ADR first.
- Schemas in `specs/` change only with ADR + CHANGELOG + `docs/protocol.md` update.

## Workflow

1. One milestone at a time (SPEC §24). **Never start the next milestone without explicit user approval.**
2. Per milestone: short design note → smallest complete vertical slice → tests → quality gate → docs → self-review vs layering/determinism/security → update `PROJECT_STATUS.md` → completion report (`/report`).
3. Work in small commits on a branch (`m<n>/<topic>`), conventional commit messages (`feat(git): …`, `test(policy): …`). Don't push or open PRs unless asked.
4. Ambiguity → smallest defensible choice → ADR in `docs/adr/NNNN-*.md` → test pinning it → note in report.
5. Blocked → stop and report with evidence. Never pretend.

## Testing rules

- Integration tests use **real git** in `tmp_path` repos built by fixture builders, and **real pytest** subprocesses for M5. Mocks only for unit-testing callers, and say so.
- Hypothesis for graph traversal, serialization round-trips, and policy invariants.
- Never delete, skip, or weaken a test to go green. Known failures: `xfail(strict=True, reason=...)` + listed in PROJECT_STATUS.
- Paste real command output in reports. Never summarize a run you didn't do.

## Completion report template (end of every milestone)

```
## M<n> completion report
Status: complete | partial | blocked
Files created/changed: <grouped list>
Design: <3–6 bullets>
Decisions: <ADR links>
Commands run + results: <each gate command → pass/fail, counts>
Tests: <N passed, N failed, N xfail>; coverage <x%>
Lint / types / layering: <results>
Known issues & limitations:
Unverified: <e.g. CI on GitHub not yet run>
Next milestone: M<n+1> — <scope>, awaiting approval
```
