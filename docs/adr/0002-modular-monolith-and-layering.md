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
- `tests/unit/test_packages.py` pins the subpackage set and the docstring-only rule.
- Adding a top-level subpackage requires updating both the layers contract and that test.
