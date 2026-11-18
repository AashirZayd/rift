# ADR-0004: Canonical JSON, content-derived IDs and timestamps

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M1
- **Spec refs:** §6.3, §7

## Context

§7 fixes the canonical JSON settings, the ID shape (`<prefix>_<24 hex>`), the volatile fields and
the timestamp format. It leaves open how to treat floats, deep nesting, where volatile-field
stripping happens, and how a model obtains its own ID.

## Decision

1. **Canonical JSON** (`rift.core.canonical`) applies the §7.1 settings (sorted keys, `(",", ":")`,
   `ensure_ascii=False`, `allow_nan=False`). It also enforces the following, with a pre-check
   that is iterative so it survives deep nesting:
   - **Floats are rejected**, not just NaN/Infinity. Counts and durations are integers (§7.1), and
     confidence is an ordinal enum (§6.3), so a float in RIFT data is always a bug. The same rule
     applies inside `FrozenMap` model data and on decode.
   - Non-string keys, sets, bytes, other objects and lone surrogates (not encodable as UTF-8) are
     rejected with `NonCanonicalValueError` (`non_canonical_value`).
   - A `RecursionError` from pathological nesting is converted into that same structured error
     (§21 "deeply nested JSON").
2. **IDs** (`rift.core.ids.content_id`) hash the canonical bytes of the payload after recursively
   removing the §7.4 volatile keys (`generated_at`, `collected_at`, `started_at`, `finished_at`,
   `duration_ms` at any depth; `hostname` only directly under `environment`).
   `rift.core.identity` derives a model's ID from its serialized form with the ID field itself
   removed (`evidence_id`, `claim_id`, `plan_id`, `proof_id`, plus `with_*_id` helpers).
3. **Timestamps** are aware UTC datetimes with `microsecond == 0`, checked at construction, and
   serialized as exactly `YYYY-MM-DDTHH:MM:SSZ`. Years are zero-padded explicitly, because
   `strftime("%Y")` does not pad on every platform. Parsing accepts only that exact form.
   `SystemClock` truncates to seconds; `FixedClock` exists for tests and reproducible runs.
4. **`hostname`** is omitted from the environment dict when not recorded (absent, not `null`).
   That keeps the default output free of the field entirely.

## Alternatives considered

- **Allow finite floats.** Python's float repr is deterministic, but floats invite fake precision
  (the very reason §6.3 uses an ordinal enum) and no field needs one.
- **Volatile stripping per model.** That's easy to forget for nested runs and evidence; a single
  recursive rule is harder to get wrong.

## Consequences

Pinned by:
- `tests/unit/core/test_canonical.py` (exact bytes, float/type rejection, deep nesting)
- `tests/unit/core/test_ids.py` (format, a known sha256 vector, volatile stripping)
- `tests/unit/core/test_models_serialization_identity.py` (proof IDs ignore every volatile field)
- `tests/contract/test_determinism.py` (byte identity across `PYTHONHASHSEED` 0 and 4242, with a
  positive control showing that raw set order does differ between the two seeds)
