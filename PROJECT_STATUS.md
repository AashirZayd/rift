# Project Status
_Last updated: 2026-11-18 — by: Claude Code session (M2 build, review fixes, gate)_

## Current milestone
M2 — Python analyzer — complete, awaiting review on `m2/python-analyzer` (M1 accepted by user 2026-11-18; CI green incl. git-minimum 2.30.2 and lowest-direct; PR #2 merged)

## Completed (verified)
### M0 (accepted)
- Packaging, CLI surface (`rift --help`, `rift version [--json]`), single-sourced version, wheel smoke test, docstring-only subpackages, 8 layering contracts with planted-violation tests, schema-mirror drift test — verified by the M0 tests, all still passing in the M1 gate.

### M1 (accepted 2026-11-18)
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

### M2 (awaiting review)
- `AnalysisResult` + `Diagnostic` models with strict codecs and Hypothesis strategies; collections sorted by documented keys and checked at construction; edge/unresolved sources must belong to the file — verified by `tests/unit/core/test_analysis_models.py`, `tests/property/test_model_roundtrip.py` (`HYPOTHESIS_PROFILE=ci`: 72 passed)
- Discovery: sorted, iterative, never follows symlinks/junctions, default + glob exclusions, venv detection by `pyvenv.cfg`, `src/` autodetection (not when `src/__init__.py`) — verified by `tests/unit/discovery/test_discovery.py` (junction test runs on Windows; symlink tests skip locally, run on Linux/macOS CI)
- §9.1 input handling: oversized (5 MiB, streaming sha256), bad coding cookie / invalid UTF-8 / BOM-vs-cookie, null bytes, RecursionError and parser stack overflow, syntax errors with position and interpreter version, syntax newer than the interpreter; siblings still analyzed — verified by `tests/unit/analysis/test_input_handling.py`
- §9.2/§9.3 extraction and resolution: symbol kinds incl. test kinds, constants, decorators, redefinition spans, `__all__` literal/non-literal, namespace packages, relative imports incl. beyond top level, TYPE_CHECKING and try/except ImportError flags, external vs missing, certain/likely/possible call tiers, inherits, references, scoping — verified by `tests/unit/analysis/test_extraction.py`
- §9.4 every dynamic behavior recorded with its reason or flag; literal `import_module("x.y")` → likely + `dynamic` — verified by `tests/unit/analysis/test_dynamic_behavior.py` (35 tests)
- Never imports/executes/compiles analyzed code: canary package, conftest, sitecustomize, usercustomize, setup.py, test module never fire; no `__pycache__`/`.pyc`; `sys.modules` untouched; positive control via a real import; AST scan bans execution primitives in `analysis`/`discovery` — verified by `tests/security/test_analysis_never_executes.py`
- Determinism: AnalysisResults byte-identical under PYTHONHASHSEED 0 vs 4242 (second run on a copy at a different absolute path) with a set-order positive control; real results round-trip `to_dict`/`from_dict` — verified by `tests/contract/test_determinism.py`
- Fresh-context review: 13 findings (1 blocker, 3 major, 7 minor, 2 nit groups) all fixed with regression tests — verified by `tests/unit/analysis/test_review_regressions.py` (20 passed, 1 skipped on Windows)
- §23 gate after review fixes: sync OK, format 130 files clean, ruff 0, mypy 0 issues in 108 files, lint-imports 8 kept / 0 broken, pytest 559 passed / 7 skipped / 1 xfailed, coverage 94.92% (fail_under 85), build OK

## Pending
- User review of M2.

## Release blockers (v0.1.0)
- Revisit typer cap before v0.1.0 — accept transitive pydantic or keep cap + document isolated install (uv tool / pipx).

## M4 checklist
(Recorded only; not implemented before M4.)
- The analyzed file set comes from git (tracked + untracked-not-ignored) intersected with discovery, assembled in the services layer, so .gitignored dirs such as venv/ and build output are never analyzed. Ties into the core.excludesFile ADR on the M6 checklist.

## M6 checklist
(Recorded only; not implemented before M6.)
- Enforce sorted collections in the models (deferred M1 review item): require sorted order (or document the key) for `ImpactResult.unresolved`, `ChangeProof.limitations`, `Evidence.limitations`, `ProofVerification.runs`, `VerificationPlan.reasons` (§7.2).
- Submodule edits are not detected (ADR-0006): this must appear in the proof's `limitations` whenever submodules are present, never silently.
- `core.excludesFile` makes untracked-file detection machine-dependent (a §7 risk). Decide with an ADR: ignore global excludes, or record a hash of them in the environment block.
- jsonschema floor 4.0.1: if M6 needs cross-file `$ref` via the `referencing` API (4.18+), raise the floor in an ADR (amend ADR-0003) rather than keeping two code paths.
- Accept and preserve `x-` extension keys in decoding (§16).

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
- Skipped on Windows: symlink tests (git untracked symlink, discovery file/dir symlinks, POSIX symlinked-dir read) need privileges, and unreadable-file/dir tests need POSIX permissions; all run on Linux/macOS CI.
- typer capped `>=0.28,<0.29` (ADR-0003). Root-package import from `rift.core` covered by an AST test (ADR-0002).
- Analyzer (ADR-0007): lambdas/comprehensions share the enclosing scope (over-approximated locals); calls through variables bound to functions (`cb = f; cb()`) are not resolved to their target but are recorded as `unknown` (ADR-0007 item 9a); attribute calls with no project method of that name are not recorded (item 9); `.pyi` stubs ignored; newer-syntax detection is a per-line heuristic; results depend on the interpreter grammar (recorded as `python_version`).
- Monkeypatching and `globals()/locals()/vars()` writes are recorded as `dynamic_attribute`; `sys.modules` injection as `dynamic_import` (no dedicated `UnresolvedReason`; ADR-0007).
- Discovery does not honour `.gitignore` (deterministic, independent of global excludes); exclusion config wiring is M7 (ADR-0008).
- Read containment: check-then-open race narrowed by `(st_dev, st_ino)` comparison; residual on filesystems with unreliable inode numbers (ADR-0008).
- Edges carry no evidence IDs until the evidence engine (M6).

## Failing / xfail tests
- `tests/contract/test_schema_mirror.py::test_expected_schemas_exist_in_both_locations_byte_identical` — `xfail(strict=True)`, ADR-0003: no schemas until M6; remove the marker then.

## Stubs
- None. Nothing from M3+ implemented (no graph construction or traversal; no SQLite cache — deferred to M5 by ADR-0007 §29).

## Unverified
- CI on GitHub for the M2 branch (Linux/macOS legs: symlink tests; Python 3.12/3.13 legs: the newer-syntax test uses PEP 758 there; git-minimum and lowest-direct). Only Windows + Python 3.11.9 was run locally.
- M1: none — user reports CI green on GitHub for PR #2, including `git-minimum` (git 2.30.2, which runs `tests/security/test_git_hardening.py::test_partial_clone_never_fetches_or_runs_transport_commands`) and `lowest-direct`.

## Architectural decisions (ADR index)
- [ADR-0001](docs/adr/0001-record-architecture-decisions.md) — Record architecture decisions
- [ADR-0002](docs/adr/0002-modular-monolith-and-layering.md) — Modular monolith, enforced layering (8 contracts), entry point `rift.cli.app:app`
- [ADR-0003](docs/adr/0003-dependencies-build-backend-and-name.md) — typer <0.29, hatchling, schemas in `rift.core.schemas` + `specs/` mirror, name `rift-cli`; M1 amendment: floors jsonschema>=4.0.1, rich>=13.8.0 and how CI checks them
- [ADR-0004](docs/adr/0004-canonical-json-ids-and-timestamps.md) — Canonical JSON (no floats), content IDs, volatile fields, timestamps
- [ADR-0005](docs/adr/0005-core-model-conventions.md) — Model conventions, strict decoding, node-ID grammar, path and remote-URL policy, line-range semantics, provisional vocabularies
- [ADR-0006](docs/adr/0006-git-layer-hardening-and-minimum-version.md) — Git hardening beyond §8 (no network, submodules, filters), determinism, minimum git 2.30
- [ADR-0007](docs/adr/0007-python-analyzer-model-and-resolution.md) — AnalysisResult/Diagnostic, edge sources and confidence tiers, §9.4 reason mapping, input handling, cache deferred to M5; review amendment (memoized bounded resolution, rebinding, static builtins)
- [ADR-0008](docs/adr/0008-discovery-and-source-roots.md) — Link-safe discovery, exclusions, source-root autodetection, read containment, pytest-style sibling imports

## Next tasks
- Await "M2 accepted".
- M3 — Dependency graph (§10): build the graph from M2 `AnalysisResult`s, Hypothesis properties, deterministic serialization. Not started.
