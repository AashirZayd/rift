# ADR-0005: Core model conventions and provisional vocabularies

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M1
- **Spec refs:** §5, §6, §13, §16, §29

## Context

§6 lists minimum fields for every model but leaves validation strength, decoding strictness, some
enum vocabularies and a few semantics unspecified.

## Decision

1. **One module per domain area** in `rift.core.models` (repo, change, source, graph, impact,
   verification, evidence, policy, proof, plugin). Every model is
   `@dataclass(frozen=True, slots=True, kw_only=True)` with tuples, frozensets and `StrEnum`s.
2. **Validation at construction.** Every `__post_init__` first calls `check_types(self)`. That is a
   shallow runtime check of each field against its annotation: an enum, not a plain string; a tuple,
   not a list; a bool is not accepted as an int; Optional unions are handled. The model-specific
   invariants follow (examples: repo-relative paths, the node-ID grammar, `LineRange` bounds,
   UTC second-precision timestamps, ID prefixes, sorted-and-unique ID collections). Without the
   type check, a wrong value only surfaced later, as a crash inside serialization.
3. **Explicit, strict serialization** (`rift.core.serialization`). Every model has a hand-written
   encoder and decoder; there is no reflection. `from_dict` rejects missing keys, unknown keys
   and wrong types, and every failure, including invariant violations, surfaces as
   `SerializationError`. The `x-` extension keys (§16) are M6 protocol work and are not accepted
   yet.
4. **Node IDs** follow the §6.3 grammar exactly: `file:<path>`, `py:<module>[:<qualname>]`,
   `test:<path>::...`. Refinements:
   - The ID prefix must agree with `NodeKind`. `class`, `function`, `method` and `constant`
     (added after M3) need `py:<module>:<qualname>`. `api_contract` has no grammar in v0.1, so it
     may use any valid ID form.
   - Qualname segments may be the CPython markers `<locals>`, `<lambda>`, and so on.
   - Limitation: a test ID is split at the **first** `::`, so a test path that contains `::` or ends
     in `:` cannot be represented faithfully (`":" + "::A" == ":::A"`). Such an ID is rejected when
     the part before the first `::` is not a valid path. Otherwise it is accepted with the
     first-`::` split, e.g. `dir:x/t.py::test` is fine. Hypothesis found this case; real test files
     do not use these names.
   - The constructor is named `node_id_for_test`, because pytest collects any function named
     `test*`.
5. **Repo paths** reject only what cannot be normalized or is absolute on some platform:
   - empty, `.`/`..` and empty segments, and NUL;
   - a leading `/`, a Windows drive (`C:`, `C:/…`, `C:\…`) and a `\\` UNC prefix.
   Spaces, newlines, backslashes, unicode, an inner `:` and a leading `-` are legal file names and
   are kept. Trade-off: a top-level directory literally named `C:` (possible on Linux) cannot be
   represented.
6. **Line ranges.**
   - `ChangedFile.changed_lines` is sorted, with overlapping and adjacent ranges merged.
   - A pure deletion inside a modified file has no new lines. It is recorded as the single
     adjacent new line `max(c, 1)`, so the enclosing symbol still counts as changed. That is the
     conservative choice: over-reporting impact never hides a change.
   - Edge case: a file emptied to zero lines gets the anchor `(1, 1)`, a line that no longer
     exists. This is kept deliberately (still conservative): impact analysis treats it as "the top
     of the file changed".
   - Deleted files use old-file line numbers (§6.1). Binary files have no ranges.
7. **Provisional vocabularies**, to be finalized with the proof schema in M6. Adding values is a
   minor protocol change; removing one needs an ADR.
   - `ClaimKind` = `file_changed | node_affected | test_selected | test_executed |
     requirement_evaluated`.
   - `ClaimStatus` = `confirmed | inferred | unknown`, mirroring `Epistemic` so a claim never
     outranks its evidence.
   - `PluginKind` = one value per §18 protocol.
8. **Epistemic honesty**: an `Evidence` with `epistemic=fact` must have `confidence=certain`
   (§2.2: never present an inference as confirmed).
9. **`Repository.root` is absolute and machine-specific**, so it is for in-process use only and is
   never embedded in a proof (§7.7). Its codec exists for in-process round-trips only.
   **`ProofSubject.remote_url` policy** (§7.7, §21) rejects:
   - credentials: any password, or any http(s) userinfo, since tokens are often passed as the
     username;
   - query strings and fragments, where tokens also travel;
   - local locations: `file:` URLs, absolute or relative paths, and drive paths, which expose
     machine paths and usernames;
   - unparseable URLs.
   Network URLs and the scp form `git@host:org/repo` are allowed.
10. **Hostile input never escapes as a raw exception.**
    - `from_dict` converts every `RecursionError`, `ValueError` and `TypeError` into
      `SerializationError`.
    - `freeze` caps nesting at 256 levels.
    - Digits in timestamps, SemVer and git versions are ASCII-only (`[0-9]`, not `\d`), so a
      decoded document always re-encodes byte-identically.
11. **Volatile-field stripping** (ADR-0004) never descends into the opaque user-data fields `data`
    and `attributes`. A key named `duration_ms` inside evidence data is content.
12. `FrozenMap` is a value container, not a domain model. It takes its single `entries` field
    positionally and is built via `FrozenMap.of(mapping)`.
13. `ChangeSet.includes_uncommitted` is true exactly when `head.commit is None`, and the base must
    be a commit.

## Alternatives considered

- **Pydantic or reflection-based codecs.** Ruled out by §3.1 and §6.
- **Lenient decoding that ignores unknown keys.** That hides protocol drift; §16 wants unknown
  non-`x-` keys rejected.

## Consequences

- `tests/property/test_model_roundtrip.py` checks that every dataclass in `rift.core.models` has a
  codec and a Hypothesis strategy, and that each one round-trips through `to_dict`/`from_dict` and
  through canonical JSON (a fixed point).
- `tests/unit/core/test_models_serialization_identity.py` pins the invariants and the strict
  decoding.
