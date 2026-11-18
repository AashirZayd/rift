# ADR-0008: File discovery, link policy and source roots

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M2
- **Spec refs:** §4, §9.3, §20, §21.2

## Context

§4 assigns file enumeration, exclusions and the symlink policy to `rift.discovery`. §9.3 asks
for source-root autodetection ("`src/` if it contains packages, else repo root"). ADR-0006
requires discovery to adopt the git layer's rule: never follow links.

## Decision

1. `discover_python_files(root, exclude=())` walks the filesystem iteratively and visits entries
   in sorted order. It returns sorted repo-relative POSIX paths of regular `*.py` files.
   - It does not consult git or `.gitignore`. The result is therefore independent of the user's
     `core.excludesFile`, which avoids the machine-dependence ADR-0006 notes for untracked files.
2. **Links are never followed**: symlinks, Windows junctions and mount points (reparse tags).
   Each one becomes a `symlink_skipped` limitation, unless excluded.
   - Other reparse points, such as cloud placeholders, are treated as ordinary entries.
   - A directory whose resolved path leaves the root is also skipped (`outside_root`).
3. **Default exclusions** (directories, never descended into, no limitation recorded):
   - any name starting with `.`;
   - `__pycache__`, `node_modules` and `*.egg-info`;
   - any directory containing a `pyvenv.cfg` file. A venv is detected by its marker, not its
     name, so a package called `env` is still analyzed.
4. **User exclusions** are glob patterns matched with `fnmatchcase` against the repo-relative
   path, so `*` crosses `/`. A leading `**/` also matches at the top level. Config wiring (§20
   `exclude`) is M7; the analyzer accepts `AnalysisOptions.exclude` now.
5. These become limitations, and the walk continues:
   - unreadable directories or entries: `unreadable_path`, with the errno name only;
   - names that are not valid UTF-8 or not valid repo paths: `unrepresentable_path`.
6. **Source roots**: `"."` means the repository root. `detect_source_roots` returns `("src",)`
   if any discovered file under `src/` has an importable module name, otherwise `(".",)`.
   - "Importable" covers a regular package, a namespace package and a single top-level module.
   - Configured roots override detection and are validated as `"."` or a repo-relative path.
7. Files outside every source root (tests and scripts beside a `src/` layout) are still
   analyzed. Their module names are derived from the repository root.
   - When two files claim one module name, priority goes: configured root over fallback, then
     package `__init__.py` over same-named `.py`, then path order.
   - The losing file gets a `module_shadowed` limitation.

## Alternatives considered

- **`git ls-files` for enumeration:** honours `.gitignore`, but makes discovery depend on git
  (a sibling layer) and on the user's global excludes. Rejected.
- **Excluding `build/`, `dist/`, `env/` by name:** these can be real package names. Rejected in
  favour of the venv marker; users can exclude more themselves.
- **Only analyzing files under source roots:** tests would never be analyzed. Rejected.

## Consequences

- Pinned by `tests/unit/discovery/test_discovery.py`: sorting, noise and venv exclusion, globs,
  symlinks, junctions, unreadable directories, and root detection.
- Also pinned by `tests/unit/analysis/test_modules_and_syntax.py`: naming priority and
  shadowing.
- Symlink tests need privileges on Windows and skip there; they run on Linux/macOS CI. The
  junction test runs on Windows.
