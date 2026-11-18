# Project Status
_Last updated: 2026-11-18 — by: Claude Code session (M1 build)_

## Current milestone
M1 — Core models & Git — in progress: gate green; independent review running (M0 accepted by user 2026-11-18; CI green, PR #1 merged)

## Completed (verified)
### M0 (accepted)
- Packaging, CLI surface (`rift --help`, `rift version [--json]`), single-sourced version, wheel smoke test, docstring-only subpackages, 8 layering contracts with planted-violation tests, schema-mirror drift test — verified by the M0 gate and tests listed in the M0 report (unchanged and still passing).

### M1
- All §6 models (33 serializable dataclasses across `rift.core.models.*`) with construction-time type and invariant checks — verified by `tests/unit/core/test_models_serialization_identity.py` (invariants, wrongly typed fields)
- Serialization round-trip for every model, through `to_dict`/`from_dict` and through canonical JSON (fixed point) — verified by `tests/property/test_model_roundtrip.py` (also asserts every dataclass in `rift.core.models` has a codec and a strategy); run under the `ci` Hypothesis profile too (500 examples, derandomized): 68 passed
- Strict decoding (unknown/missing keys, wrong types, invalid enums, floats in data → `SerializationError`) — verified by `tests/unit/core/test_models_serialization_identity.py`
- Canonical JSON per §7.1 (floats rejected, deep nesting → structured error) — verified by `tests/unit/core/test_canonical.py`
- Content-derived IDs and volatile-field stripping per §7.3/§7.4 — verified by `tests/unit/core/test_ids.py` (incl. sha256 vector) and identity tests (proof ID ignores every volatile field)
- Determinism across `PYTHONHASHSEED` 0 and 4242 (canonical JSON + IDs of real-git ChangeSets and set-built models), with a positive control that raw set order differs — verified by `tests/contract/test_determinism.py`
- Clock (injected, UTC, second precision) and RFC 3339 format/parse; `RiftError` hierarchy — verified by `tests/unit/core/test_clock_paths_errors.py`
- `run_git` per §8 (argv list, exact hardening args/env, absolute git from absolute PATH entries, `--no-pager`, closed stdin, structured errors incl. timeout) — verified by `tests/unit/git/test_parsing_version_runner.py`
- Minimum git 2.30 checked by `require_supported_git` — verified by unit tests (version parsing/policy) and `tests/integration/git/test_repository_revisions.py::test_installed_git_is_supported` (local git 2.56.1). The 2.30.2 run itself is the `git-minimum` CI job (not yet run, see Unverified)
- Repository discovery, shallow detection, default branch, current revision (detached, unborn, dirty incl. untracked/ignored), revision validation (branches, tags incl. annotated, short SHA, non-commits rejected), base resolution (explicit → default → merge-base origin/HEAD; clear error otherwise), shallow-clone `ShallowHistoryError` — verified by `tests/integration/git/test_repository_revisions.py` (real git)
- Change detection commit↔commit and commit↔working tree: modified (exact line ranges incl. pure deletions), added, deleted (old-side ranges), renamed (pure and edited), similarity threshold, type change, binary, staged/unstaged/untracked, ignored excluded, exotic names (space, unicode, newline, tab, quote, backslash, colon, glob chars, leading `-`, `--output=x.py`), hostile user git config → identical output — verified by `tests/integration/git/test_changes.py` (real git)
- Security: fsmonitor / diff.external / textconv / filter canaries in `.git/config` never run during any public rift.git call, each with a positive control in plain git; pager config never invoked; revision option/command injection rejected at every entry point; non-UTF-8 names refused; planted git executable never run — verified by `tests/security/test_git_hardening.py` (26 tests)
- Dependency floors `jsonschema>=4.0.1`, `rich>=14.2.0`: the whole suite passes at exactly those versions — verified locally by `uv sync --resolution lowest-direct` in a clean clone (jsonschema 4.0.1, rich 14.2.0, typer 0.28.0) + `RIFT_EXPECT_LOWEST_DIRECT=1 uv run --no-sync pytest` → 360 passed, 1 skipped, 1 xfailed
- §23 gate: format 102 files clean, ruff 0, mypy 0 issues in 82 files, lint-imports 8 kept, pytest 359 passed / 2 skipped / 1 xfailed, coverage 95.98% (fail_under 85 enforced), build OK, wheel smoke OK

## Pending
- Independent review of the M1 diff, fixes, re-run of the gate
- Completion report

## Release blockers (v0.1.0)
- Revisit typer cap before v0.1.0 — accept transitive pydantic or keep cap + document isolated install (uv tool / pipx).

## M9 checklist
- Fill CoC contact (`CODE_OF_CONDUCT.md` reporting placeholder).
- Enable GitHub private vulnerability reporting on the repository (SECURITY.md points to it).

## Known limitations
- New CI jobs (`git-minimum`, the floor assertion in `lowest-direct`) have not run on GitHub yet. Docker Desktop's engine was not running locally, so the git 2.30.2 container run is unverified until CI.
- `rich` floor is 14.2.0, not typer's 13.8.0: dev tool import-linter 2.16 requires rich ≥ 14.2.0, so nothing older can be tested (ADR-0003 amendment).
- If M6 needs jsonschema's `referencing` registry, the jsonschema floor rises to 4.18.
- `--exclude-standard` honours the user's `core.excludesFile`, so untracked-file sets can differ across machines (ADR-0006).
- Line ranges cost one git call per changed file (ADR-0006); batching deferred.
- Pager canary has no positive control (git pages only on a TTY).
- Untracked-symlink test is skipped on Windows (symlink creation needs privileges); it runs on Linux/macOS CI.
- Test node IDs whose path contains `:` are rejected as ambiguous (ADR-0005).
- typer capped `>=0.28,<0.29` (ADR-0003).
- Root-package import from `rift.core` covered by an AST test, not import-linter (ADR-0002).

## Failing / xfail tests
- `tests/contract/test_schema_mirror.py::test_expected_schemas_exist_in_both_locations_byte_identical` — `xfail(strict=True)`, ADR-0003: no schemas until M6; remove the marker then (XPASS fails the suite).

## Stubs
- None. Nothing from M2+ implemented.

## Architectural decisions (ADR index)
- [ADR-0001](docs/adr/0001-record-architecture-decisions.md) — Record architecture decisions
- [ADR-0002](docs/adr/0002-modular-monolith-and-layering.md) — Modular monolith, enforced layering (8 contracts), entry point `rift.cli.app:app`
- [ADR-0003](docs/adr/0003-dependencies-build-backend-and-name.md) — typer <0.29, hatchling, schemas in `rift.core.schemas` + `specs/` mirror, name `rift-cli`; **M1 amendment: floors jsonschema>=4.0.1, rich>=14.2.0 and their CI proof**
- [ADR-0004](docs/adr/0004-canonical-json-ids-and-timestamps.md) — Canonical JSON (no floats), content IDs, volatile fields, timestamps
- [ADR-0005](docs/adr/0005-core-model-conventions.md) — Model conventions, strict decoding, node-ID grammar, line-range semantics, provisional vocabularies
- [ADR-0006](docs/adr/0006-git-layer-hardening-and-minimum-version.md) — Git hardening beyond §8, determinism flags, minimum git 2.30

## Next tasks
- Finish M1 review/report; await "M1 accepted".
- M2 — Python analyzer (§9) incl. cache-placement decision (§30). Not started.
