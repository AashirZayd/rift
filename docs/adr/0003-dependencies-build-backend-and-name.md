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
- No schema exists in M0, so nothing is created yet. This is implemented, and the sync test added,
  when the first schema lands (M6, or earlier if config validation arrives first).

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
