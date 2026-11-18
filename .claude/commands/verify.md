---
description: Run the full RIFT quality gate and report real results
---

Run each command separately, in order, and record the real outcome of each (do not stop at the first failure — run them all so the report is complete):

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
uv build
```

Then report a table: command → pass/fail → key numbers (tests passed/failed/xfail, coverage %, mypy error count, files reformatted).

If anything fails: diagnose and fix the cause (not the test, not the threshold, not the lint config — unless the config itself is wrong, in which case justify it). Re-run the gate. Never weaken a test, add a blanket `# type: ignore`/`# noqa`, or lower coverage to make it pass; any targeted ignore needs an inline reason.
