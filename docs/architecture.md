# RIFT architecture

This document describes how the implemented parts of RIFT fit together. Decisions and their
reasons are in [`docs/adr/`](adr/); what is still to be built is in
[`docs/SPEC.md`](SPEC.md) and [`PROJECT_STATUS.md`](../PROJECT_STATUS.md).

## Layers

```
cli → services → proof → policy → verification → impact|invariants → analysis
    → graph|evidence|cache|git|discovery|config|plugins → core
```

A lower layer never imports a higher one, and siblings joined by `|` never import each other.
import-linter enforces this in CI (ADR-0002).

## Impact pipeline (SPEC §11, ADR-0010)

```
git (detect_changes, list_worktree_files)
  └─ services.analysis.analyzed_file_set   git listing ∩ discovery
       └─ analysis.analyze_files            AnalysisResult per file (ADR-0007)
            └─ graph.build_graph            DependencyGraph + unresolved + dangling (ADR-0009)
                 └─ impact.map_changes      changed nodes + module anchors
                      └─ impact.assess_impact
                           ├─ reverse traversal (imports|calls|inherits|references|depends_on)
                           ├─ related tests, public interfaces, touching unresolved references
                           ├─ classify       ordinal §11.1 tier, never a risk probability
                           └─ completeness   every reason listed as a Limitation
                                → ImpactResult
```

1. **Map** each changed file and its line ranges to nodes:
   - symbols whose own lines intersect the changed lines (for a class, its lines minus its
     members'), plus the file's module node;
   - the module node starts the traversal only for module-level edits or added, renamed or
     binary files;
   - removed symbols, found from an in-memory parse of the base side;
   - a deleted file as its `file:` node only. The result is then `partial`, since there is no
     cache before M5.
2. **Link** references that no longer resolve but name a changed or removed module or symbol to
   its node, at `likely`. A failed module-level import links every symbol of the importing file.
3. **Traverse** reverse dependencies, honouring `min_confidence` and `max_depth`. Every reached
   node gets the shortest explanation path: fewest weak edges first, then the smallest node IDs.
4. **Collect** related tests (the `test:` nodes reached), public interfaces, and the unresolved
   references whose source is in the impact set.
5. **Classify** on the ladder `none < local < module < cross_package < public_api`. The rungs are
   decided by dependency edges inside the impact set that cross a module boundary, so more
   changed nodes never give a lower tier. Test code never counts.
6. **Completeness** is orthogonal to the tier. Every reason is listed:
   - unresolved references touching the impact set;
   - files that could not be analyzed;
   - deleted files without prior analysis;
   - changed stubs.

The head of a change is always the working tree, because analysis reads files from disk. The
base side of modified and renamed files is read from git objects only to find removed symbols.
