# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/). The Change Proof protocol is versioned
separately (see `docs/SPEC.md` §16).

## [Unreleased]

### Added
- Project foundation (M0): `src/` package layout with one docstring-only subpackage per
  architectural module, `rift --help` and `rift version [--json]`.
- Tooling: uv lockfile, hatchling build, Ruff, mypy (strict), pytest with coverage, Hypothesis
  profiles, import-linter layering contracts.
- CI workflow: Linux/macOS/Windows × Python 3.11–3.13, full quality gate, wheel smoke test;
  a `lowest-direct` job runs the tests against the minimum allowed direct dependencies.
- Contract test keeping the `specs/` schema mirror byte-identical to the packaged schemas.
- Project documents: LICENSE (MIT), README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ADRs 0000–0003.
