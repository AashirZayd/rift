# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/). The Change Proof protocol is versioned
separately (see `docs/SPEC.md` §16).

## [Unreleased]

### Changed
- Runtime dependency floors: `jsonschema>=4.0.1`, `rich>=14.2.0` (ADR-0003 amendment).
- Coverage threshold `fail_under = 85` is enforced.

### Added
- Core (M1): every SPEC §6 model as frozen dataclasses with construction-time validation;
  explicit strict `to_dict`/`from_dict` serialization; canonical JSON; content-derived IDs with
  volatile fields stripped; injected `Clock` with RFC 3339 UTC timestamps; `RiftError` hierarchy.
- Git layer (M1): hardened `run_git` (absolute git from absolute `PATH` entries, SPEC §8 hardening,
  repository filter drivers neutralized), minimum git 2.30, repository discovery, current revision,
  revision validation, base resolution, and change detection for commit↔commit and
  commit↔working tree, including untracked files.
- CI: `git-minimum` job (git 2.30.2 in Debian bullseye); the `lowest-direct` job now asserts that it
  installed the declared floors.
- Project foundation (M0): `src/` package layout with one docstring-only subpackage per
  architectural module, `rift --help` and `rift version [--json]`.
- Tooling: uv lockfile, hatchling build, Ruff, mypy (strict), pytest with coverage, Hypothesis
  profiles, import-linter layering contracts.
- CI workflow: Linux/macOS/Windows × Python 3.11–3.13, full quality gate, wheel smoke test;
  a `lowest-direct` job runs the tests against the minimum allowed direct dependencies.
- Contract test keeping the `specs/` schema mirror byte-identical to the packaged schemas.
- Project documents: LICENSE (MIT), README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ADRs 0000–0003.
