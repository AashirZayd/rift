# ADR-0002: Modular monolith with enforced layering

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M0
- **Spec refs:** §0, §4, §4.1, §19

## Context

RIFT ships as a single distribution with a `src/` layout (§0). Its correctness guarantees
(deterministic core, a proof builder free of analysis logic, a thin CLI, a public plugin API that
depends only on core) are architectural. They only hold if the import structure enforces them.

## Decision

1. **One package, one subpackage per module in §4.** In M0 every subpackage `__init__.py` holds
   only a docstring stating its single responsibility. Code goes in named modules, never in
   `__init__.py`. This keeps re-export surfaces explicit.
2. **Layers are enforced by import-linter** (`[tool.importlinter]` in `pyproject.toml`), run by
   `uv run lint-imports` in the quality gate and CI:
   - A `layers` contract mirroring §4.1, with `|` for independent siblings (L2, L4). It is
     `exhaustive`, so a new top-level subpackage fails until it is placed in a layer.
     `rift.__main__` is excluded; it is only the `python -m rift` shim.
   - `forbidden` contracts for the specific rules:
     - `rift.core` imports nothing from `rift`.
     - `rift.plugins.api` imports only `rift.core`.
     - `rift.proof` never imports `analysis`, `graph`, `impact`, `verification` or `git`.
       **Indirect imports count too** (import-linter default): proof must not reach those modules
       through `rift.policy`. This is the strictest reading of §16. It may be relaxed later, but
       only by a superseding ADR.
     - `rift.cli` imports only `rift.services` and `rift.core`, **checked on direct imports only**
       (`allow_indirect_imports = true`), because cli → services → lower layers is the intended path.
     - **Only `rift.cli` renders**: no other subpackage may import `rich` or `typer`. This encodes
       "policy must not import … any renderer" (§4.1) and "output goes through `rift.cli`
       renderers" (§5). It requires `include_external_packages = true`.
   - An `acyclic_siblings` contract over all of `rift`, for "no circular imports" (§4.1). It
     covers cycles inside a package as well (e.g. between two `rift.core` modules), which the
     top-level layers contract cannot see.
   - A `layers` contract inside `rift.plugins` (`(registry) | (trust)` above `api`, exhaustive).
     The forbidden contract cannot name `rift.plugins` itself, because it is the ancestor of
     `rift.plugins.api`. Without this layers contract, `api` could import the registry and trust
     code (§2.5).
   - **Induced rule (from the transitive proof ban):** `rift.policy`, and any module `rift.proof`
     imports, must not reach `analysis`, `graph`, `impact`, `verification` or `git`. The proof
     validator re-evaluates the verdict through policy (§16), so policy has to work on core
     models and evidence only. If M6 needs otherwise, a superseding ADR must relax this
     deliberately.
   - **Known gap:** import-linter cannot forbid the ancestor package `rift` itself, so
     `rift.core` → `rift` (e.g. `from rift import __version__`) is pinned by an AST test
     instead (`tests/unit/test_packages.py::test_core_never_imports_the_root_package`).
     `rift.services.version` legitimately imports the root package for `__version__`.
3. **Entry point is `rift.cli.app:app`**, not `rift.cli:app`. Rule 1 forbids defining the Typer
   app in `rift/cli/__init__.py`.
4. **Even `rift version` goes through a service** (`rift.services.version.version_info`), so the
   rule "each command calls exactly one service function" (§19) holds from the first command.
5. `rift.plugins.api` exists in M0 as a docstring-only module so its contract applies from M0.

## Alternatives considered

- **Multiple distributions (core, cli, plugins).** Rejected by §0, and it adds release overhead.
- **Review-only enforcement.** Layer drift is gradual and easy to miss in review.

## Consequences

- `tests/contract/test_layering.py` runs `lint-imports` on the real tree. It also plants forbidden
  imports in a temporary copy (core→cli, graph→analysis, graph→git, impact→invariants, cli→graph,
  proof→analysis, proof→policy→verification, plugins.api→cache, an unplaced subpackage) and asserts
  that the expected contract reports BROKEN. This proves the contracts actually catch violations.
  Further planted cases cover an intra-package cycle, api→registry, an unplaced plugins module,
  policy→rich and services→typer.
- `tests/unit/test_packages.py` pins the subpackage set and the docstring-only rule.
- The hand-maintained forbidden lists (cli, core, plugins.api, renderer sources) are compared
  against the real subpackage set by `tests/contract/test_layering.py`. A new top-level package
  cannot silently become importable from those sources.
- **Adding a top-level subpackage requires updating:** the §4.1 layers contract, the `core`,
  `plugins.api` and `cli` forbidden lists, the renderer contract's sources, and `SUBPACKAGES` in
  `tests/unit/test_packages.py`. The drift tests fail until all of these are done.
