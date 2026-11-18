# ADR-0003: Dependency budget, build backend, schema packaging and distribution name

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M0
- **Spec refs:** §0, §3, §4, §30

## Context

§30 leaves three M0 questions open: the build backend, the final PyPI name, and how JSON schemas
are packaged. §3 fixes the runtime dependency budget and rules out Pydantic.

## Decision

### 1. Runtime dependencies: `typer>=0.28,<0.29`, `rich`, `jsonschema`

No Pydantic: internal models are frozen dataclasses, and boundaries are validated with JSON Schema.

**Observed on 2026-11-18:** `typer` 0.29.0 added a hard dependency on `pydantic` (and with it the
compiled `pydantic-core`). Resolving each version with `uv pip compile` for Python 3.11 showed:

| typer | brings |
|---|---|
| 0.20.0 – 0.24.0 | `click` |
| 0.26.0 – 0.28.0 | neither (click is vendored) |
| 0.29.0 | `pydantic==2.14.3` |

We cap typer at `<0.29`, so the installed runtime set stays within the §3.1 budget and contains no
Pydantic, not even transitively. The lower bound is 0.28 because it is the newest version without
Pydantic. Lifting the cap is a dependency decision and needs a superseding ADR.

### 2. Build backend: `hatchling`

§3.4 wants the version single-sourced. hatchling reads it dynamically from `src/rift/__init__.py`
(`[tool.hatch.version]`). `uv_build` (stable as of uv 0.11) has no dynamic-version support, so it
would need a second copy of the version in `pyproject.toml`. hatchling is a build-time dependency
only. `tests/unit/test_version.py::test_version_is_single_sourced` pins this: installed metadata
must equal `rift.__version__`.

### 3. Schema packaging: canonical copy inside the package, mirror in `specs/`

- The canonical schema files will live in **`src/rift/core/schemas/`**. They are data files, read
  with `importlib.resources`, and they ship in the wheel. Hatchling includes every file under
  `src/rift`.
- `specs/` holds byte-identical mirrors for readers and external tooling. A contract test will
  assert that the two copies are identical.
- **This deviates from the spec's suggested `src/rift/proof/schemas/`.** `rift.config` (L2)
  validates against `config.schema.json`. Loading that resource from `rift.proof` (L7) would make
  a lower layer depend on a higher one. `rift.core` is the lowest layer, so every layer may read it.
- **The schema loader also lives in core** (`rift.core.schemas`: locate and parse a packaged
  schema by name with stdlib `importlib.resources` + `json`). `rift.config`, `rift.invariants` and
  `rift.proof` all call it. This **amends §4**, which lists schema loading under `rift.proof`.
  `rift.proof` still owns proof *validation* (schema + referential + verdict consistency); only
  locating and loading the file moves down.
- No schema exists in M0, so nothing is created yet. This is implemented, and the sync test added,
  when the first schema lands (M6, or earlier if config validation arrives first).
- The sdist `include` already lists `/specs`. This is harmless while the directory doesn't exist,
  and ships the mirror once it does.

### 4. Distribution name: `rift-cli`

PyPI JSON API checked on 2026-11-18:

| Name | Result |
|---|---|
| `rift-cli` | 404, not registered (`/simple/rift-cli/` also 404) |
| `rift-proof` | 404, not registered |
| `riftcheck` | taken (v1.0.2, uploaded 2026-05-15, unrelated project) |
| `rift` | taken (unrelated project) |

We use `rift-cli`. The import package and the command stay `rift`. A 404 shows the name was free on
that date, not that it is reserved. Availability is only final at first upload (M9). `rift-proof`
is the fallback.

### 5. JUnit XML parsing (recorded now, implemented in M5)

The stdlib `xml.etree.ElementTree` is used with no `defusedxml`. The XML is produced by our own
pytest invocation into a temporary file we control, and the stdlib parser does not expand external
entities. M5 must still bound the file size and handle corrupt XML as evidence, not as a pass.

## Alternatives considered

- **`uv_build`.** Simpler and faster, but no single-sourced version.
- **Allow typer 0.29+ with transitive Pydantic.** No direct use, but it ships a heavy compiled
  dependency the budget never anticipated.
- **Schemas under `rift.proof`.** Causes the layering problem described above.

## Consequences

- `uv.lock` pins the exact resolution. CI installs with `uv sync --locked`.
- The typer cap must be revisited if a typer security fix lands only in ≥ 0.29.

## Amendment (M1, 2026-11-18): runtime dependency floors

Recorded here at the user's request. This adds to Decision §1 above and replaces none of it.

| Dependency | Floor | Why this floor |
|---|---|---|
| `jsonschema` | `>=4.0.1` | 4.0.x is the first line with `Draft202012Validator` (3.2.0 has none). **4.0.0 is yanked** on PyPI ("Improper python_requires declaration"), so resolvers skip it under `>=4.0.0`; the real floor is 4.0.1. |
| `rich` | `>=14.2.0` | typer 0.28 alone would allow `rich>=13.8.0`, but the dev tool import-linter 2.16 requires `rich>=14.2.0`. An environment that can run the test suite therefore cannot install anything older, so 14.2.0 is the oldest version we can *prove*. We don't claim a floor CI cannot exercise. |
| `typer` | `>=0.28,<0.29` | Unchanged (Decision §1). |

**Proof.** The `lowest-direct` CI job (ubuntu, Python 3.11) runs `uv sync --resolution lowest-direct`
and then the whole suite with `RIFT_EXPECT_LOWEST_DIRECT=1`. That enables
`tests/unit/test_dependency_floors.py::test_lowest_direct_job_really_installed_the_floors`, which
fails unless:
- every direct dependency is installed exactly at its declared floor, and
- the loosened floors (rich, jsonschema) are older than the versions in the committed `uv.lock`.

`test_jsonschema_draft_2020_12_features_rift_relies_on` exercises the 2020-12 features the M6
schemas will need: `$defs`/`$ref`, `prefixItems` with `items: false`, `patternProperties` for `x-`
extensions, and `unevaluatedProperties: false`.

Local run on 2026-11-18 (Windows, Python 3.11.9): lowest-direct resolved `jsonschema 4.0.1`,
`rich 14.2.0`, `typer 0.28.0`; 360 passed, 1 skipped (symlink test, needs Windows privileges),
1 xfailed (schema mirror, pending M6).

**Consequences.**
- If M6 needs jsonschema's `referencing` registry for cross-file `$ref`, the jsonschema floor rises
  to 4.18 with a superseding note here.
- The floor test pins the declared floors exactly, so changing a floor also means updating that test.
