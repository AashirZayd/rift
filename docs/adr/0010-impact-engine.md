# ADR-0010: Impact engine: analyzed file set, change mapping, classification and completeness

- **Status:** accepted
- **Date:** 2026-11-19
- **Milestone:** M4
- **Spec refs:** §6.4, §11, §19 (`rift impact`), §24 M4

## Context

§11 gives the algorithm and the §11.1 tier table. It leaves open:
- which files are analyzed;
- what "changed symbols", "reverse dependents" and "their own module" mean exactly;
- how deletions reach their users without a cache;
- where completeness reasons live: the M1 `ImpactResult` had only a flag;
- how `--symbol` resolves names.

## Decision

### Inputs

1. **Analyzed file set** (`rift.services.analysis`): the files git lists (`ls-files --cached
   --others --exclude-standard`: tracked plus untracked-not-ignored) **intersected** with
   discovery (link-safe `.py` files after exclusions, ADR-0008).
   - A `.gitignore`d `venv/` or build output is never analyzed, even without `pyvenv.cfg`.
   - An untracked nested repository is not part of this repository.
   - A tracked file that is deleted from disk is not read.
   - Discovery limitations are kept only for paths git lists.
   - This set depends on the user's `core.excludesFile` exactly as untracked-file detection
     does. The ADR on the M6 checklist decides that for both.
2. **Head is the working tree.** Analysis reads files from disk, so impact is computed from the
   base to the working tree (staged, unstaged, untracked). The base comes from SPEC §8. A
   revision that is not checked out cannot be analyzed in v0.1.

### Mapping changes to nodes (§11 step 1, `rift.impact.mapping`)

3. Facts per node (`rift.impact.index`):
   - file, module and **unit**. The unit is the top-level package; a module-less file is its
     own unit;
   - the symbol, if any;
   - **test code**: the file matches pytest's `test_*.py` / `*_test.py` (the analyzer's rule,
     `analysis.naming.is_test_path`).
4. Per changed file:
   - **Modified**: the symbols whose span (decorators included) intersects a changed range,
     plus the file's own node (its module node, or its file node if it has no module name).
   - **Added / copied**: every symbol plus the own node.
   - **Renamed**: the same rules, under the new path.
   - **Deleted `.py`**: `file:<path>` only, with reason `deleted_without_analysis`; there is no
     cache before M5.
   - **Whitespace-only edits** count as changes. git reports the lines, and RIFT does not guess
     which edits are behaviour-neutral.
   - Non-Python, ignored and excluded files map to nothing.
5. **Module anchors.** The analyzer resolves names against the current files only. So code that
   still names a removed or renamed module, or a removed member of a changed module, yields an
   unresolved reference (e.g. `missing_module`, raw `pkg.gone` or `pkg.m.removed_fn`), not an
   edge.
   - Each changed or removed module gets an anchor node:
     - changed module → its own node;
     - deleted file → its `file:` node;
     - renamed file → the new own node, under the old module name.
   - An unresolved reference whose `raw` names an anchored module (exactly or as a dotted
     prefix; the most specific anchor wins) links its source to the anchor, at `likely`, kind
     `references`. A dangling edge into an anchored module links the same way, with its own kind
     and confidence.
   - These links exist only in a working copy of the graph; the build's graph is never
     modified.
   - The unresolved references themselves stay listed and make the result `partial`.

### Traversal, tests, interfaces (§11 steps 2–5, `rift.impact.assess`)

6. Reverse traversal from the changed nodes:
   - over `imports | calls | inherits | references | depends_on`;
   - honouring `min_confidence` (default `possible`) and `max_depth` (default unlimited);
   - with ADR-0009's strongest-then-smallest explanation paths.

   Collected from the traversal:
   - `affected_nodes`: everything reached that is not changed;
   - `related_tests`: the `test:` nodes reached, changed tests included;
   - `unresolved`: every unresolved reference whose source is in the impact set (changed plus
     affected);
   - `public_interfaces`: symbol nodes in the impact set that are public (8);
   - an explanation path for every affected node and related test.
7. **Classification** (`rift.impact.classify`). It is ordinal and never a risk probability.
   - Definitions:
     - C: the changed nodes that are not test code; S: the symbols among them;
     - D_out: affected nodes that are not test code and lie outside every module of C.
   - The highest match wins:

     | Tier | Rule |
     |---|---|
     | `public_api` | some s in S is public and its unit is a public package |
     | `cross_package` | D_out is non-empty and C ∪ D_out spans two or more units |
     | `module` | D_out is non-empty (one unit) |
     | `local` | D_out is empty |
     | `none` | no changed nodes at all |
   - "Reverse dependents" in the `module` and `cross_package` rules means D_out: dependents
     outside the changed nodes' own modules. Otherwise a private helper with a caller in its own
     module, which §11.1 describes as `local`, would match `module` first.
   - `local` therefore also covers §11.1's "all changed symbols private …" and the leftover case
     of a public symbol outside the public packages with no outside dependents: the change stays
     in its module.
   - Test code is excluded from C and D_out. A test in `tests/` must not turn every tested
     change into `cross_package`. Tests are reported as `related_tests` instead.
   - A test-only change is `local`.
8. **Public.**
   - A symbol is public when no segment of its module path or qualname is private: `_x` is
     private, `__x__` is public, and a `<locals>`-style segment is private. Test code is never
     public.
   - Public packages default to every top-level package of non-test code
     (`project.public_packages = []` in SPEC §20). `ImpactSettings.public_packages` overrides
     this.
   - Module nodes are not "symbols" here: a module-level-only change cannot be `public_api`.

### Completeness (§11, model change)

9. `ImpactResult.limitations: tuple[Limitation, ...]` lists **every** reason the result is not
   complete. The model enforces that it is empty exactly when `completeness=complete`.
   Reasons:

   | Kind | Subject | When |
   |---|---|---|
   | `unresolved_reference` | the reason | at least one unresolved reference with that reason touches the impact set; the message gives the count, and the references are in `unresolved` |
   | `analysis_failed` | file path | the file has an error diagnostic (parse or decode failure) |
   | analyzer and discovery kinds (`oversized_file`, `symlink_skipped`, ...) | path | the file was skipped |
   | `deleted_without_analysis` | file path | a deleted file had no prior analysis |

   - A file that failed to analyze makes the result `partial` wherever it is: its
     dependencies are unknown.
   - `unknown` is not produced in M4. Analysis that cannot run raises a structured
     `RiftError`; `unknown` is for a proof that must still be written (M6).
   - The model also now requires sorted `unresolved` and `limitations`, `test:` IDs in
     `related_tests`, and an explanation for every affected node and related test.
   - This is a pre-protocol change: SPEC §6.4 and the CHANGELOG are updated, and the protocol is
     first published in M6.

### `--symbol` (`rift.impact.lookup`) and services

10. A query is matched in tiers; the first tier with any match decides:
    1. full name: node ID, `module:qualname`, `module.qualname`, a module's name, a test's
       pytest ID;
    2. exact qualname;
    3. short (last-segment) name.

    One match wins. Several matches return every candidate, sorted, and `match=None`: the
    caller must choose (the CLI exits 2 in M7). No match raises
    `SymbolNotFoundError(symbol_not_found)` with a hint; an empty query raises
    `invalid_argument`.
11. Services (`rift.services.impact`) take `ImpactOptions`, which mirror the §20 keys. Wiring
    config files is M7.
    - `impact_of_changes(root, base)` returns a `ChangeImpact(changes, impact)`.
    - `resolve_symbol(root, query)` returns a `SymbolLookup`.
    - `impact_of_nodes(root, nodes)` serves a resolved `--symbol`: a hypothetical change of
      exactly those nodes.
    - `impact_of_paths(root, paths)` serves `rift impact PATH...`: every line of those files
      changed. An unanalyzed path is an `ImpactError`.

## Alternatives considered

- **Discovery alone (M2) as the file set.** Rejected: it analyzes ignored venvs and build
  output.
- **Literal "highest match wins" with all dependents.** Rejected: `local` would be unreachable
  whenever a private helper has a same-module caller (7).
- **Count test code as dependents.** Rejected: almost every tested change would become
  `cross_package`.
- **Mark the result `partial` for every dynamic import anywhere in the project.** Rejected for
  v0.1: most projects would never be `complete`. Only references whose source is in the impact
  set count, which is a known limitation.
- **A boolean completeness flag.** Rejected: §11 requires the reasons, and they feed the M5
  full-suite decision and M6 proof limitations.

## Consequences

Pinned by:
- `tests/integration/services/test_analyzed_file_set.py`: real git; ignored venv and build
  output, a force-added file, a deleted tracked file, a nested repository;
- `tests/unit/impact/test_classification.py`: each tier, each neighbouring boundary, publicity
  rules, test-code exclusion, no probability language;
- `tests/unit/impact/test_completeness_and_tests.py`: every reason, orthogonality to the tier,
  related tests under `min_confidence` and `max_depth`;
- `tests/unit/impact/test_lookup.py`: every query tier, ambiguity, not-found;
- `tests/unit/core/test_impact_model.py`: model invariants;
- `tests/integration/services/test_impact.py`: end to end with real git for add, modify,
  delete, rename, whitespace-only, non-source and ignored changes, and the symbol and path
  services;
- `tests/contract/test_determinism.py`: impact under PYTHONHASHSEED 0 vs 4242, on a copy at
  another path.

Known limitations:
- Without the M5 cache, a deleted file's own symbols are unknown, and a deleted file's tests are
  found only through code that still names the module.
- An unresolved reference outside the impact set (e.g. `import_module(name)` elsewhere) can hide
  a dependent without making the result `partial`.
- Test code is recognised by pytest's default file naming only.
- Head must be the working tree.
