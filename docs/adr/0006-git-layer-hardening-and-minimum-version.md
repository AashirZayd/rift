# ADR-0006: Git layer hardening, determinism and minimum git version

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M1
- **Spec refs:** §8, §21, §30 ("minimum supported git version")

## Context

§8 specifies `run_git`, the hardening arguments and environment, `-z` parsing and revision
validation. §30 leaves the minimum git version open. Implementing §8 exposed three gaps:
- a repository-configured filter driver still runs,
- user config can change diff output,
- Git for Windows rewrites `\` inside pathspecs.

## Decision

### Minimum git version: 2.30 (a hard floor)

§8 mandates `git rev-parse --verify --end-of-options <rev>^{commit}`. `rev-parse` learned
`--end-of-options` only in **git 2.30.0** (`Documentation/RelNotes/2.30.0.adoc`: *"git rev-parse"
learned the "--end-of-options" to help scripts to safely take a parameter that is supposed to be a
revision*). git 2.24 had added it to the generic option parser only (`2.24.0.adoc`). Every other
feature used is older:

| Feature | Since |
|---|---|
| `rev-parse --is-shallow-repository` | 2.15 |
| `config --show-scope` | 2.26 |
| `init -b` (test fixtures only) | 2.28 |

`require_supported_git()` raises `GitUnavailableError(code=git_too_old)` below 2.30. It is called by
`open_repository`, and `rift doctor` (M7) will surface it. **Proof:** the `git-minimum` CI job runs
the whole suite in `python:3.11-bullseye`, which has git 2.30.2, after asserting that
`git --version` is 2.30.x.

### Hardening beyond the §8 list

`HARDENING_ARGS` and the environment match §8 exactly (a test pins them). In addition:

1. **Executable resolution.** git is resolved from absolute `PATH` entries only, and `run_git`
   executes that absolute path. This blocks a planted `git`/`git.exe` in the analyzed repository
   or the current directory. Windows process creation searches the current directory first, and
   relative `PATH` entries would do the same.
2. **`--no-pager`, plus a closed stdin.**
3. **Repository filter drivers are neutralized.**
   - Problem: a `filter.<name>.clean` command from `.git/config` runs during working-tree
     comparisons even with the §8 hardening. A canary test proved this.
   - Fix: for working-tree commands, RIFT lists `filter.*` entries via
     `git config --show-scope --includes -z`, which reads config and executes nothing. Every
     driver from `local` or `worktree` scope is overridden with empty `clean`, `smudge` and
     `process` commands and `required=false`.
   - The user's own global and system filters (e.g. git-lfs) are trusted (§21.1) and left alone.
   - A driver name containing `=` cannot be expressed as `-c key=value` on git 2.30, so such a
     repository is refused with `unsafe_repository`. `GIT_CONFIG_COUNT` would need 2.31.
4. **Revisions**
   - A leading `-` and any NUL, CR or LF are rejected before git runs.
   - `--end-of-options` is still passed, as a second line of defence.
   - In a shallow clone, an unresolvable revision raises `ShallowHistoryError` with a
     fetch-depth hint.

### Determinism of change detection

- Every diff pins: `--no-ext-diff --no-textconv --no-color`, `-M<n>%` (default 50), `-l32767`,
  `--diff-algorithm=myers`, `--indent-heuristic`, `--inter-hunk-context=0`, `--no-relative`,
  `--ignore-submodules=none`.
- A test with a hostile `~/.gitconfig` checks that the output is byte-identical. The hostile
  config sets histogram diffs, `noprefix`, `relative`, `renames=copies`, `renameLimit=1`, colors,
  `quotePath`, and `showUntrackedFiles=no`.
- **No pathspecs for committed content.**
  - Files come from `diff --raw -z --no-abbrev`, which carries full blob IDs.
  - Hunks are computed blob↔blob (`git diff -U0 <old> <new>`), and whole-file ranges come from
    `cat-file blob`.
  - Reason: Git for Windows rewrites `\` in pathspecs, even with `:(literal)`, so a path such as
    `back\slash.py` from a Linux commit would silently get no ranges. A test caught this.
  - Pathspecs are used only for working-tree content git has not hashed, and on Windows the
    working tree cannot contain such names.
- **Untracked files** come from `ls-files --others --exclude-standard`.
  - They count as dirty, and ignored files don't.
  - Their content is read with `lstat`: a symlink is never followed, and a non-regular file is
    never opened.
  - Binary detection uses git's 8000-byte NUL heuristic.
- **Non-UTF-8 file names** raise `undecodable_path`. Canonical JSON cannot represent them, and
  guessing an encoding would be dishonest.

## Known limitations

- `--exclude-standard` honours the user's `core.excludesFile`, so the set of untracked files can
  differ between machines (§8 specifies `--exclude-standard`).
- `GIT_DIR`, `GIT_WORK_TREE` and similar are inherited from the environment, because §8 says
  "inherited minus nothing".
- Line ranges need one `git` call per changed file. This is fine for M1 and can be batched later.
- No positive control is possible for pager canaries: git pages only on a TTY.

## Consequences

- `tests/security/test_git_hardening.py`
  - fsmonitor, `diff.external`, textconv and filter canaries, each with a positive control in
    plain git
  - injection attempts through every revision entry point
  - non-UTF-8 names and a planted executable
- `tests/integration/git/*`: real repositories covering renames, deletions, type changes, a
  detached HEAD, a shallow clone, a dirty tree, untracked and ignored files, exotic names (space,
  unicode, newline, tab, quote, backslash, colon, leading `-`, `--output=x.py`), and the
  hostile-config test.
