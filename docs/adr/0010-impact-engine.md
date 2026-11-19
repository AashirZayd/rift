# ADR-0010: Impact engine: analyzed file set, change mapping, classification and completeness

- **Status:** accepted
- **Date:** 2026-11-19
- **Milestone:** M4
- **Spec refs:** §6.4, §11, §19 (`rift impact`), §24 M4

## Context

§11 gives the algorithm and the §11.1 tier table. It leaves open:
- which files are analyzed;
- what "changed symbols", "reverse dependents" and "their own module" mean exactly;
- how removals and deletions reach their users without a cache;
- where completeness reasons live (the M1 `ImpactResult` had only a flag);
- how `--symbol` resolves names.

## Decision

### Inputs

1. **Analyzed file set** (`rift.services.analysis`): the files git lists (`ls-files --cached
   --others --exclude-standard`, i.e. tracked plus untracked-not-ignored), **intersected** with
   discovery (link-safe `.py` files after exclusions, ADR-0008).
   - A `.gitignore`d `venv/` or build output is never analyzed, even without `pyvenv.cfg`.
   - An untracked nested repository is not part of this repository.
   - A tracked file that is deleted from disk is not read.
   - Discovery limitations are kept only for paths git lists.
   - The set depends on the user's `core.excludesFile` exactly as untracked-file detection does.
     The M6 ADR decides that for both.
2. **Head is the working tree.** Analysis reads files from disk, so impact runs from the base to
   the working tree (staged, unstaged, untracked); the base comes from SPEC §8. The **base side**
   of modified, renamed, type-changed and deleted `.py` files is read from git objects
   (`git.read_files_at`: literal pathspecs, no fetching) and parsed in memory
   (`analysis.analyze_sources`, never executed), only to find removed symbols (4). Comparing two
   commits that are not checked out is not supported. On the M7 checklist: it must fail with a
   structured error (or be supported), never return a result computed from the working tree.

### Mapping changes to nodes (§11 step 1, `rift.impact.mapping`)

3. **Facts per node** (`rift.impact.index`):
   - the file, module and **unit**. The unit is the top-level package; a module-less file is
     its own unit;
   - the symbol;
   - the analyzer's visibility of the symbol's top-level ancestor (it honours `__all__`);
   - whether the file is under the source roots (as opposed to the analyzer's fallback naming);
   - whether it is **test code**: a pytest test file (`test_*.py` / `*_test.py`), a
     `conftest.py`, or any file under a `tests/` or `test/` directory.

   A deleted or moved-away file's module name is the analyzer's (`ModuleIndex`, fallback naming
   and shadowing included).
4. **Per changed file:**
   - **Modified**: a symbol is changed when its *own lines* intersect a changed range.
     - A function's own lines are its whole span, decorators included.
     - A class's own lines are its span minus its members' spans, so editing a method changes
       the method, not the class.
     - The file's own node (its module node, or its file node if it has no module name) is
       always changed.
   - **Removed symbols**: the base side's symbols whose nodes no longer exist are changed nodes.
     That covers a removed function, and every symbol of a module that moved to a new name. They
     join the working graph as nodes.
   - **The own node starts the reverse traversal** (so that its importers become dependents)
     only in these cases:
     - module-level code changed: a changed line outside every top-level symbol;
     - the file was added, copied, renamed or type-changed;
     - the file is binary;
     - a symbol was removed from it.

     Otherwise a symbol-only edit reaches only the code that uses the changed symbols.
   - **Added / copied / binary**: every symbol is changed.
   - **Renamed**: the same rules, under the new path. A rename to a path that is not analyzed is
     handled as a deletion of the old path.
   - **Deleted `.py`** (decided by the user after the M4 review): every node of the file's
     base-side analysis, i.e. its module node and all its symbols, so a deleted public symbol is
     public API removed. Only if the base side cannot be parsed (no blob, a parse or decode
     failure, oversized) does it fall back to `file:<path>`, with reason
     `deleted_without_analysis`. Deleted paths that the exclusions match map to nothing, like
     their modifications.
   - **`.pyi`**: stubs are not analyzed (ADR-0007), so a changed stub adds reason
     `stub_not_analyzed`.
   - **Whitespace-only edits** count as changes; RIFT does not guess which edits are
     behaviour-neutral.
   - Non-Python, ignored and excluded files map to nothing.
5. **Anchors.** Code that still names a removed or renamed module, or a removed symbol, yields an
   unresolved reference (e.g. `missing_module`, raw `pkg.gone` or `pkg.m.removed_fn`), because
   the analyzer resolves against the current files only.
   - Each changed module, removed symbol, deleted file and renamed-away module name has an anchor
     node.
   - An unresolved reference whose `raw` or one of its dotted prefixes names an anchor (the most
     specific wins) links its source to the anchor, at `likely`, kind `references`. A dangling
     `py:` edge into an anchor links the same way, with its own kind and confidence.
   - When the source is a module or file node (a failed module-level import), every symbol of
     that file is linked too: the whole module fails to import, its tests included.
   - Links exist only in a working copy of the graph (`DependencyGraph.copy`). The unresolved
     references stay listed and make the result `partial`.

### Traversal, tests, interfaces (§11 steps 2–5, `rift.impact.assess`)

6. Reverse traversal from the mapping's starts:
   - over `imports | calls | inherits | references | depends_on`;
   - honouring `min_confidence` (default `possible`) and `max_depth` (default unlimited);
   - with ADR-0009's strongest-then-smallest explanation paths.

   Collected from the traversal:
   - `affected_nodes`: everything reached that is not changed;
   - `related_tests`: the `test:` nodes in the impact set (changed plus affected);
   - `unresolved`: the unresolved references whose source is in the impact set;
   - `public_interfaces`: the public symbols in the impact set, removed ones included;
   - one explanation per affected node and related test. Each starts at a changed node; a
     changed test explains itself.

### Classification (§11.1, `rift.impact.classify`)

7. Classification is ordinal and never a risk probability.
   - **Crossing dependency**: an edge `s -> t` of a traversed kind and at least
     `min_confidence`, where both ends are in the impact set, neither is test code, and they lie
     in different modules. Edges between two changed nodes count too.
   - The highest match wins:

     | Tier | Rule |
     |---|---|
     | `public_api` | a changed or removed symbol is public in a public package, or a deleted or moved-away file whose base side could not be parsed has a public module path in a public package (its symbols are unknown, so they are assumed public) |
     | `cross_package` | the crossing dependencies' ends span two or more units |
     | `module` | some crossing dependency exists |
     | `local` | no crossing dependency |
     | `none` | no changed nodes |
   - This is how §11.1's "reverse dependents [outside their own module]" and "≥ 2 top-level
     packages" are measured. Counting dependents relative to *every* changed module would lower
     the tier when a caller in another package is changed too. Counting every changed unit would
     make two unrelated changes `cross_package`.
   - With crossing dependencies the tier is **monotone**: more changed nodes never give a lower
     tier. A Hypothesis property checks this.
   - Test code is excluded, so that tested changes do not all become `cross_package`; tests are
     reported as `related_tests`. A test-only change is `local`.
8. **Public.**
   - A symbol is public when all of these hold:
     - no segment of its module path is private (`_x`; dunders are public);
     - the analyzer reports its top-level ancestor as not private: `__all__` decides when it is
       a literal, otherwise leading underscores do;
     - no nested qualname segment is private or `<locals>`-style;
     - it is not test code.
   - Public packages default to every top-level package of non-test code **under the source
     roots** (SPEC §11.1). Fallback-named code outside them (e.g. `tests/` in a `src/` layout) is
     never public. `ImpactSettings.public_packages` overrides the default with a validated tuple
     of names.

### Completeness (§11, model change)

9. `ImpactResult.limitations` lists every reason the result is not complete, plus
   informational entries that do not affect completeness. The model enforces that a `partial` or
   `unknown` result lists at least one reason. Kind constants live in `rift.impact.mapping` and
   `rift.impact.assess`. Reasons:

   | Kind | Subject | When |
   |---|---|---|
   | `unresolved_reference` | the reason | at least one unresolved reference with that reason touches the impact set; the count is in the message, and the references are in `unresolved`. `external_module` does **not** count (user decision): an external import cannot hide a project dependent. Guard: a reference whose top-level name is one of the project's own packages or modules (any source root) always counts, and a broken internal import is `missing_module` anyway |
   | `analysis_failed` | file path | the file has an error diagnostic (parse or decode failure), wherever it is |
   | `oversized_file` and discovery skips (`symlink_skipped`, ...) | path | the file was skipped, wherever it is |
   | other analyzer kinds (`non_literal_all`, `module_shadowed`, ...) | path | only when the file is in the impact set |
   | `deleted_without_analysis` | file path | a deleted file had no prior analysis |
   | `stub_not_analyzed` | file path | a changed `.pyi` |

   Informational (does not affect completeness):

   | Kind | Subject | When |
   |---|---|---|
   | `dynamic_code_in_project` | none | some file anywhere in the project has a non-literal dynamic import or code loading (`dynamic_import`, `module_getattr`, `sys_path_mutation`, `exec_eval`). One entry; the message names the files, sorted |

   On the M5 checklist: an f-string or concatenation import whose literal prefix matches a
   changed module's package will make the result `partial` and trigger the full suite.

   - `unknown` is not produced in M4: analysis that cannot run raises a structured `RiftError`.
   - The model also requires:
     - at least one limitation when the result is not `complete`;
     - sorted `unresolved` and `limitations`;
     - `test:` IDs in `related_tests`;
     - changed and affected nodes disjoint;
     - related tests and public interfaces inside the impact set;
     - an explanation for every affected node and related test, starting at a changed node;
     - `none` exactly when nothing changed.
   - This is a pre-protocol change (SPEC §6.4, CHANGELOG). The first version required
     `limitations` to be empty exactly when `complete`; that was relaxed for informational entries.

### `--symbol` (`rift.impact.lookup`) and services

10. A query is matched in tiers; the first tier with any match decides:
    1. full name: node ID, `module:qualname`, `module.qualname`, a module's name, a test's
       pytest ID;
    2. exact qualname;
    3. short (last-segment) name.

    One match wins. Several matches return every candidate, sorted, and `match=None`: the
    caller must choose (the CLI exits 2 in M7). No match raises
    `SymbolNotFoundError(symbol_not_found)` with a hint.
11. Services (`rift.services.impact`) take `ImpactOptions`, which mirror the §20 keys. Wiring
    config files is M7.
    - `impact_of_changes(root, base)` returns a `ChangeImpact`.
    - `resolve_symbol(root, query)` resolves a `--symbol` query.
    - `impact_of_nodes(root, nodes)` computes a resolved symbol's impact. It accepts analyzed
      nodes only and raises `unknown_node` otherwise.
    - `impact_of_paths(root, paths)` serves `rift impact PATH...`.

### Amendment after the fresh-context review (same milestone)

The first version:
- classified by dependents outside *all* changed modules, so a tier could drop when more lines
  changed (now 7);
- never saw removed symbols or public modules moved by a rename (now 2 and 4);
- started every traversal at the module node, so `local` was unreachable for imported modules
  (now 4);
- ignored `__all__` (now 8);
- counted `tests/` and fallback-named code as public packages and as production dependents
  (now 3 and 8);
- lost renames out of the analyzed set (now 4);
- missed the tests of deleted modules (now 5);
- made unrelated non-failure limitations global (now 9);
- treated a method edit as an edit of its class (now 4).

### Decisions after M4 review (user)

- `external_module` references do not affect completeness, with the project-name guard (9).
- Deleted files use the base-side parse; the file-node-only fallback is only for a base side that
  cannot be parsed (4).
- Project-wide non-literal dynamic imports are reported once, informationally (9). The
  prefix heuristic is on the M5 checklist.

## Alternatives considered

- **Discovery alone (M2) as the file set.** Rejected: it analyzes ignored venvs and build
  output.
- **Dependents relative to every changed module** (the first version). Rejected: not monotone
  (7).
- **Deleted files as file nodes only until the M5 cache** (the first version). Superseded by
  the user: the base-side parse gives their symbols without a cache.
- **`external_module` makes the result `partial`** (the SPEC §11 literal reading). Rejected by
  the user: almost every real result would be `partial`, and an external import cannot hide a
  project dependent.
- **Count test code as dependents.** Rejected: almost every tested change would become
  `cross_package`.
- **Mark the result `partial` for every dynamic import anywhere in the project.** Rejected for
  v0.1: only references whose source is in the impact set count, which is a known limitation.
- **A boolean completeness flag.** Rejected: §11 requires the reasons.

## Consequences

Pinned by:
- `tests/integration/services/test_analyzed_file_set.py`;
- `tests/integration/services/test_impact.py`: end to end with real git for add, modify,
  delete, rename (removed public API), whitespace-only, non-source and ignored changes, plus the
  symbol and path services;
- `tests/unit/impact/test_classification.py`: each tier, each boundary, publicity, test code,
  the word-bounded probability scan;
- `tests/unit/impact/test_completeness_and_tests.py`;
- `tests/unit/impact/test_lookup.py`;
- `tests/unit/impact/test_engine_details.py`;
- `tests/unit/impact/test_review_regressions.py`: every review finding, plus the monotonicity
  property;
- `tests/unit/impact/test_user_decisions.py`: stdlib and third-party imports, a broken internal
  import, the project-name guard, the dynamic-code entry;
- `tests/unit/core/test_impact_model.py`;
- `tests/contract/test_determinism.py`: PYTHONHASHSEED 0 vs 4242, on a copy at another path.

Known limitations:
- If a deleted file's base side cannot be parsed, its symbols are unknown, and a public module
  path is assumed to be public API.
- An unresolved reference outside the impact set (e.g. `import_module(name)` elsewhere) can hide
  a dependent without making the result `partial`. It is reported by the informational
  `dynamic_code_in_project` entry; the M5 prefix heuristic will narrow this.
- Test code is recognised by path conventions only.
- Head must be the working tree.
