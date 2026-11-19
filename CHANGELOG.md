# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/). The Change Proof protocol is versioned
separately (see `docs/SPEC.md` §16).

## [Unreleased]

### Pre-protocol vocabulary changes
The Change Proof protocol is first published in M6; until then, model vocabularies may
still change.
- `NodeKind.constant` (SPEC §6.3): module-level constants are graph nodes, not folded into
  their module (ADR-0009 §13).
- `ImpactResult.limitations` (SPEC §6.4): every reason an impact result is not complete is listed
  (a `partial` or `unknown` result has at least one), plus informational entries such as
  `dynamic_code_in_project`. `ImpactResult` also now requires sorted
  `unresolved`/`limitations` and an explanation for every affected node and related test
  (ADR-0010).

### Fixed
- Analyzer: these calls are now recorded as `unknown` unresolved references instead of being
  dropped (ADR-0007 item 9a):
  - calls through a local, parameter or assigned name (`cb = f; cb()`);
  - calls of a subscript or of a call result;
  - calls of a module object.

### Changed
- Runtime dependency floors: `jsonschema>=4.0.1` (4.0.0 is yanked), `rich>=13.8.0`
  (ADR-0003 amendment).
- Coverage threshold `fail_under = 85` is enforced.

### Added
- Impact engine (M4, ADR-0010; `rift.impact`, `rift.services.impact`):
  - changed lines map to nodes (symbols whose span intersects them, plus the module
    node; deleted files as their file node);
  - reverse traversal with `min_confidence` / `max_depth`;
  - the ordinal SPEC §11.1 classification as made precise by ADR-0010, i.e. module-crossing
    dependencies inside the impact set (monotone in the change set; never a risk
    probability);
  - removed symbols found from an in-memory parse of the base side of modified, renamed and
    deleted files;
  - `external_module` references are listed but do not make a result `partial` (a project's own
    top-level names are never external);
  - completeness with every reason listed;
  - related tests with explanation paths;
  - `--symbol` resolution (exact names win; an ambiguous name returns every candidate).
- Analyzed file set: git's tracked plus untracked-not-ignored files intersected with
  discovery, so `.gitignore`d venvs and build output are never analyzed
  (`rift.git.files.list_worktree_files`, `rift.services.analysis`).
- `docs/architecture.md`: the layers and the impact pipeline.
- SPEC §31 and README: a roadmap of planned additions (an offline HTML proof report, a GitHub
  Action, a separate dashboard). Planned only; not implemented.
- Dependency graph (M3, ADR-0009): `rift.graph.multigraph.DependencyGraph`, a custom in-memory
  directed multigraph with bidirectional adjacency.
  - An edge's key is `(source, kind, target, restricting flags)`: edges that differ only in
    `type_checking_only` / `conditional` are kept in parallel. Duplicates of one key merge to the
    strongest confidence, with provenance flags and evidence IDs unioned.
  - Iterative BFS `forward_reachable` / `reverse_reachable` with validated kind,
    `min_confidence` and `max_depth` filters and sorted results. Each reached node gets a
    shortest explanation path with the fewest weak edges, ties going to the smallest node IDs.
  - Deterministic, strict `to_dict` / `from_dict`.
  - `rift.graph.builder.build_graph(results)` turns M2 `AnalysisResult`s into the graph:
    - collectable test functions and methods become `test:` nodes;
    - module-level constants are `constant` nodes;
    - an edge to a missing node is kept in `dangling` and recorded as an
      `UnresolvedReference`.
- `benchmarks/test_graph_build.py`: a pytest-based benchmark that builds the graph for a
  repository (default: RIFT itself). Run it with `uv run pytest benchmarks --no-cov -s`.
- Python analyzer (M2, ADR-0007): pure static analysis with stdlib `ast`. Analyzed code is
  never imported, executed or compiled.
  - One `AnalysisResult` per file: symbols; `imports`/`calls`/`inherits`/`references` edges
    with origin and ordinal confidence; unresolved references.
  - Every SPEC §9.4 dynamic behavior is recorded.
  - Input handling: oversized files, bad encodings, null bytes, too-deep nesting, syntax errors
    and syntax newer than the interpreter.
  - Namespace packages, relative imports, `TYPE_CHECKING` and `try/except ImportError` flags,
    and literal or non-literal `__all__`.
- Discovery (M2, ADR-0008): link-safe Python file enumeration (symlinks and junctions are never
  followed), default and glob exclusions, and `src/` source-root autodetection.
- Core models: `AnalysisResult` and `Diagnostic`, with strict codecs.
- Core (M1): every SPEC §6 model as frozen dataclasses with construction-time validation;
  explicit strict `to_dict`/`from_dict` serialization; canonical JSON; content-derived IDs with
  volatile fields stripped; injected `Clock` with RFC 3339 UTC timestamps; `RiftError` hierarchy.
- Git layer (M1): hardened `run_git` (absolute git from absolute `PATH` entries, SPEC §8
  hardening, no network: `protocol.allow=never` and no lazy fetch, repository filter drivers
  neutralized, submodules never entered), minimum git 2.30, repository discovery, current revision,
  revision validation, base resolution, and change detection for commit↔commit and
  commit↔working tree (binary decided by content, untracked files and nested repositories).
- CI: `git-minimum` job (git 2.30.2 in Debian bullseye); the `lowest-direct` job now resolves
  only runtime requirements at their floors and asserts that it installed them. Neither has run
  on GitHub yet.
- Project foundation (M0): `src/` package layout with one docstring-only subpackage per
  architectural module, `rift --help` and `rift version [--json]`.
- Tooling: uv lockfile, hatchling build, Ruff, mypy (strict), pytest with coverage, Hypothesis
  profiles, import-linter layering contracts.
- CI workflow: Linux/macOS/Windows × Python 3.11–3.13, full quality gate, wheel smoke test;
  a `lowest-direct` job runs the tests against the minimum allowed direct dependencies.
- Contract test keeping the `specs/` schema mirror byte-identical to the packaged schemas.
- Project documents: LICENSE (MIT), README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ADRs 0000–0003.
