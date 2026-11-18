# Project Status
_Last updated: 2026-11-18 — by: Claude Code session (M1 build)_

## Current milestone
M1 — Core models & Git — awaiting review (M0 accepted by user 2026-11-18; CI green, PR #1 merged)

## Completed (verified)
### M0 (accepted)
- Packaging, CLI surface (`rift --help`, `rift version [--json]`), single-sourced version, wheel smoke test, docstring-only subpackages, 8 layering contracts with planted-violation tests, schema-mirror drift test — verified by the M0 tests, all still passing in the M1 gate.

### M1
- All §6 models (33 serializable dataclasses in `rift.core.models.*`) with construction-time type and invariant checks — verified by `tests/unit/core/test_models_serialization_identity.py`
- Serialization round-trip for every model (`to_dict`/`from_dict` and canonical JSON fixed point); every dataclass has a codec and a strategy — verified by `tests/property/test_model_roundtrip.py` (also run with `HYPOTHESIS_PROFILE=ci`, 500 examples: 68 passed)
- Strict decoding; hostile input (deep nesting, malformed URLs, oversized ints) surfaces only as `SerializationError` — verified by `tests/unit/core/test_models_serialization_identity.py`, `tests/unit/core/test_review_hardening.py`
- Canonical JSON per §7.1 (floats, non-dict mappings, oversized ints rejected; deep nesting structured) — verified by `tests/unit/core/test_canonical.py`, `test_review_hardening.py`
- Content IDs and volatile stripping per §7.3/§7.4 (user `data`/`attributes` never stripped) — verified by `tests/unit/core/test_ids.py`, identity tests, `test_review_hardening.py`
- Determinism across `PYTHONHASHSEED` 0 and 4242 with a positive control (raw set order differs) — verified by `tests/contract/test_determinism.py`
- Clock, ASCII-only RFC 3339 timestamps, `RiftError` hierarchy, repo-path policy incl. Windows drive/UNC rejection, `remote_url` policy — verified by `tests/unit/core/test_clock_paths_errors.py`, `test_review_hardening.py`
- `run_git` per §8 plus no-network hardening (`protocol.allow=never`, `GIT_NO_LAZY_FETCH=1`), absolute git from absolute PATH, failure classification (safe.directory, not-a-repo, missing objects) — verified by `tests/unit/git/test_parsing_version_runner.py`
- Minimum git 2.30 enforced in `open_repository` — verified by unit tests on version parsing/policy and `tests/integration/git/test_repository_revisions.py::test_installed_git_is_supported` (local git 2.56.1)
- Repository discovery, shallow detection, default branch, current revision (detached, unborn, dirty incl. untracked/ignored), revision validation, base resolution, shallow-history errors — verified by `tests/integration/git/test_repository_revisions.py` (real git)
- Change detection commit↔commit and working tree: modified (exact ranges incl. pure deletions), added, deleted, renamed (pure, edited, staged), similarity threshold, type change, content-based binary detection independent of attributes, untracked, ignored, nested repos, `rm --cached`, exotic names commit↔commit and modified in the working tree, shallow clone change detection and merge base, hostile user config incl. `core.attributesFile` → identical output — verified by `tests/integration/git/test_changes.py` (real git)
- Security: fsmonitor / diff.external / textconv / repo filter / submodule filter / partial-clone uploadpack canaries never run under any public rift.git call, each with a positive control in plain git; pager never invoked; revision injection rejected at every entry point; non-UTF-8 names refused; planted git executable never run — verified by `tests/security/test_git_hardening.py` (28 tests)
- Dependency floors `jsonschema>=4.0.1`, `rich>=13.8.0`, `typer>=0.28`: whole suite passes at exactly those versions — verified locally in a clean clone with the lowest-direct procedure (runtime-only `uv pip compile --resolution lowest-direct` → jsonschema 4.0.1, rich 13.8.0, typer 0.28.0) + `RIFT_EXPECT_LOWEST_DIRECT=1 pytest --ignore=tests/contract/test_layering.py` → 378 passed, 2 skipped, 1 xfailed
- §23 gate after review fixes: sync OK, format 103 files clean, ruff 0, mypy 0 issues in 83 files, lint-imports 8 kept / 0 broken, pytest 398 passed / 3 skipped / 1 xfailed, coverage 96.02% (fail_under 85), build OK, wheel smoke OK

## Pending
- User review of M1.

## Release blockers (v0.1.0)
- Revisit typer cap before v0.1.0 — accept transitive pydantic or keep cap + document isolated install (uv tool / pipx).

## M6 checklist
- Require sorted order (or document the key) for `ImpactResult.unresolved`, `ChangeProof.limitations`, `Evidence.limitations`, `ProofVerification.runs`, `VerificationPlan.reasons` (§7.2; M1 review nit).
- Accept and preserve `x-` extension keys in decoding (§16).
- If cross-file `$ref` needs jsonschema's `referencing`, raise the floor to 4.18 (ADR-0003).

## M9 checklist
- Fill CoC contact (`CODE_OF_CONDUCT.md` reporting placeholder).
- Enable GitHub private vulnerability reporting on the repository (SECURITY.md points to it).

## Known limitations
- `--exclude-standard` honours the user's `core.excludesFile` (untracked sets can differ across machines) (ADR-0006).
- Uncommitted content inside a submodule is not reported (submodules compared by recorded commit only, so git never runs inside them) (ADR-0006).
- Blobs read fully into memory; one or two git calls per changed file (ADR-0006).
- A file emptied to zero lines is anchored at line 1 (ADR-0005).
- Test node IDs are split at the first `::`; paths containing `::` cannot be represented (ADR-0005).
- Pager canary has no positive control (git pages only on a TTY).
- Skipped on Windows: untracked-symlink test (needs privileges) and unreadable-file test (POSIX permissions); both run on Linux/macOS CI.
- typer capped `>=0.28,<0.29` (ADR-0003). Root-package import from `rift.core` covered by an AST test (ADR-0002).

## Failing / xfail tests
- `tests/contract/test_schema_mirror.py::test_expected_schemas_exist_in_both_locations_byte_identical` — `xfail(strict=True)`, ADR-0003: no schemas until M6; remove the marker then.

## Stubs
- None. Nothing from M2+ implemented.

## Unverified
- CI on GitHub for the M1 branch, in particular the new `git-minimum` job (git 2.30.2) and the reworked `lowest-direct` job. The Docker engine was not running locally, so git 2.30 has not been exercised on this machine.

## Architectural decisions (ADR index)
- [ADR-0001](docs/adr/0001-record-architecture-decisions.md) — Record architecture decisions
- [ADR-0002](docs/adr/0002-modular-monolith-and-layering.md) — Modular monolith, enforced layering (8 contracts), entry point `rift.cli.app:app`
- [ADR-0003](docs/adr/0003-dependencies-build-backend-and-name.md) — typer <0.29, hatchling, schemas in `rift.core.schemas` + `specs/` mirror, name `rift-cli`; M1 amendment: floors jsonschema>=4.0.1, rich>=13.8.0 and how CI checks them
- [ADR-0004](docs/adr/0004-canonical-json-ids-and-timestamps.md) — Canonical JSON (no floats), content IDs, volatile fields, timestamps
- [ADR-0005](docs/adr/0005-core-model-conventions.md) — Model conventions, strict decoding, node-ID grammar, path and remote-URL policy, line-range semantics, provisional vocabularies
- [ADR-0006](docs/adr/0006-git-layer-hardening-and-minimum-version.md) — Git hardening beyond §8 (no network, submodules, filters), determinism, minimum git 2.30

## Next tasks
- Await "M1 accepted".
- M2 — Python analyzer (§9), incl. the cache-placement decision (§30). Not started.
