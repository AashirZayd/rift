# RIFT — Claude Code kickoff

## Setup (once)

```bash
mkdir rift && cd rift && git init
# unzip this kit into the repo root, so you have:
#   CLAUDE.md
#   docs/SPEC.md
#   .claude/settings.json
#   .claude/commands/{milestone,verify,report}.md
claude
```

Prereqs on your machine: Python 3.11+, `uv`, `git`.

## Paste this as the first prompt

```
We're starting RIFT. Read CLAUDE.md and docs/SPEC.md fully before doing anything.

Execute M0 only (SPEC §24 "M0 — Foundation"). Do not write any analyzer, git, graph, impact, verification, policy or proof logic.

Steps:
1. Inspect the environment: OS, `python --version`, `uv --version`, `git --version`, existing files. Report them.
2. Check PyPI name availability for `rift-cli` (fallbacks `rift-proof`, `riftcheck`). If you have no network access, ask me — don't guess. Record the result in ADR-0003.
3. Write a short M0 plan: files to create, build backend choice (hatchling vs uv_build) with reasoning, how JSON schemas will be packaged, risks.
4. Implement:
   - pyproject.toml: src layout, `rift` entry point -> `rift.cli:app`, runtime deps typer/rich/jsonschema only, dev group pytest/pytest-cov/hypothesis/ruff/mypy/import-linter, Ruff + mypy (strict) + pytest + coverage + import-linter (layers from SPEC §4.1) config.
   - src/rift/ with every subpackage from SPEC §4, each `__init__.py` holding only a docstring stating its single responsibility. `__version__ = "0.0.0"` until M9.
   - Minimal CLI: `rift --help`, `rift version` (and `--json`). Nothing else.
   - tests: version smoke, CLI help exits 0, import every subpackage, `rift version --json` stdout is exactly one valid JSON document.
   - .github/workflows/ci.yml per SPEC §23 (OS x Python matrix, contents: read, uv, full gate, wheel smoke test in a fresh venv). Pin actions to commit SHAs if you can resolve them; otherwise pin major versions and flag it as a follow-up.
   - LICENSE (MIT, "RIFT contributors"), README (honest pre-alpha), CONTRIBUTING, SECURITY (stub), CODE_OF_CONDUCT (Contributor Covenant 2.1 by reference), CHANGELOG (Unreleased), .gitignore, .editorconfig, PROJECT_STATUS.md.
   - docs/adr/0000-template.md, 0001-record-architecture-decisions.md, 0002-modular-monolith-and-layering.md, 0003-dependencies-build-backend-and-name.md.
5. Run /verify. Fix real failures. Then install the built wheel into a throwaway venv and run `rift version` from it.
6. Commit on branch `m0/foundation` in logical commits.
7. Run /report and stop. Wait for my review before M1.
```

## After you review M0

Tell Claude "M0 accepted", then:

```
/milestone M1
```
