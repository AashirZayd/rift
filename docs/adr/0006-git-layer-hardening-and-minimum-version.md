# ADR-0006: Git layer hardening, determinism and minimum git version

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M1
- **Spec refs:** §2.4, §2.6, §8, §21, §30 ("minimum supported git version")

## Context

§8 specifies `run_git`, the hardening arguments and environment, `-z` parsing and revision
validation. §30 leaves the minimum git version open. Implementing §8 and the M1 independent review
exposed gaps the §8 list does not cover:
- repository-configured filter drivers still run, including those in submodules;
- a partial clone lazily fetches from the network;
- `.gitattributes` and user config can change diff results;
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
| `protocol.allow` | 2.12 |
| `init -b` (test fixtures only) | 2.28 |

`require_supported_git()` raises `GitUnavailableError(code=git_too_old)` below 2.30. It is called by
`open_repository`, and `rift doctor` (M7) will surface it.

**Evidence.** The local suite runs on git 2.56.1. A `git-minimum` CI job runs the whole suite in
`python:3.11-bullseye` (git 2.30.2) after asserting `git --version` is 2.30.x. That job has **not
run yet**: the branch is unpushed, and the Docker engine was not running locally.

### Hardening beyond the §8 list

`HARDENING_ARGS` and the §8 environment are kept exactly (a test pins them). In addition:

1. **Executable resolution.** git is resolved from absolute `PATH` entries only, and `run_git`
   executes that absolute path. This blocks a planted `git`/`git.exe` in the analyzed repository
   or the current directory. Windows process creation searches the current directory first, and
   relative `PATH` entries would do the same.
2. **`--no-pager`, plus a closed stdin.**
3. **No network, ever (§2.4).**
   - `-c protocol.allow=never` refuses every transport, and `GIT_NO_LAZY_FETCH=1` disables lazy
     fetching outright (git >= 2.44; older versions ignore it, and the protocol ban still applies).
   - Without these, a partial clone (`--filter=blob:none`, which actions/checkout supports) lazily
     fetches missing blobs from the promisor remote. That is network access, and it runs
     repository-configured transport commands (`remote.*.uploadpack`, `core.sshCommand`).
   - A missing object now raises `missing_objects` with a hint.
4. **Repository filter drivers are neutralized.**
   - Problem: a `filter.<name>.clean` command from `.git/config` runs during working-tree
     comparisons even with the §8 hardening.
   - Fix: for working-tree commands, RIFT lists `filter.*` entries via
     `git config --show-scope --includes -z`, which reads config and executes nothing. Every
     driver from `local` or `worktree` scope is overridden with empty `clean`, `smudge` and
     `process` commands and `required=false`.
   - The user's own global and system filters (e.g. git-lfs) are trusted (§21.1) and left alone.
   - A driver name containing `=` cannot be expressed as `-c key=value` on git 2.30, so such a
     repository is refused with `unsafe_repository`.
5. **Submodules are never entered.**
   - Diffs and `status` use `--ignore-submodules=dirty`, so a submodule is compared only by its
     recorded commit, and git never runs inside it.
   - Reason: a submodule's own config (`.git/modules/<sub>/config`, e.g. a filter driver) is
     outside what RIFT neutralizes. Consequence: uncommitted content *inside* a submodule does not
     make the superproject dirty or changed.
6. **Revisions**
   - A leading `-` and any NUL, CR or LF are rejected before git runs.
   - `--end-of-options` is still passed, as a second line of defence.
   - In a shallow clone, an unresolvable object name or ancestry expression (`HEAD~3`) raises
     `ShallowHistoryError` with a fetch-depth hint, and an unknown branch name stays
     `invalid_revision`.

### Determinism and honesty of change detection

- **Pinned diff options:** `--no-ext-diff --no-textconv --no-color`, `-M<n>%` (default 50),
  `-l32767`, `--diff-algorithm=myers`, `--indent-heuristic`, `--inter-hunk-context=0`,
  `--no-relative`, `--ignore-submodules=dirty`.
- **Binary is decided by content, never by attributes.** A file is binary if there is a NUL in the
  first 8000 bytes of either side (git's own heuristic); text hunks use `--text`. Otherwise:
  - a repository `.gitattributes` (`*.py -diff`) could hide line-level changes (§2.3);
  - a user's `core.attributesFile` would make results machine-dependent (§7.6).
  A test with a hostile `~/.gitconfig` checks that output is byte-identical. It sets histogram
  diffs, `noprefix`, `relative`, `renames=copies`, `renameLimit=1`, colors, `quotePath`,
  `showUntrackedFiles=no`, and an `attributesFile` that marks `*.py` binary.
- **No pathspecs for committed content.**
  - Files come from `diff --raw -z --no-abbrev`.
  - In commit↔commit mode, hunks are computed blob↔blob and whole-file ranges come from
    `cat-file blob`.
  - Reason: Git for Windows rewrites `\` in pathspecs, even `:(literal)`.
- **Working-tree mode reads the new side from disk.** The raw "new" blob ID can be a hash git
  computed in memory (e.g. for rename detection) and never wrote. Working-tree hunks therefore use
  a `:(literal)` pathspec diff against the base, and Windows working trees cannot contain the
  names that break pathspecs.
- **Untracked files** come from `ls-files --others --exclude-standard`.
  - They count as dirty, and ignored files don't.
  - Content is read with `lstat`: a symlink is never followed, and a non-regular file is never
    opened. Unreadable files raise `unreadable_file`.
  - An untracked nested repository (listed as `dir/`) becomes one added entry with no lines.
  - A path that is deleted in the index but present on disk (`git rm --cached`, or re-created
    after `git rm`) becomes one MODIFIED entry covering its whole current content (conservative).
- **Non-UTF-8** file names, branch names or paths raise `undecodable_path`. Canonical JSON cannot
  represent them, and guessing an encoding would be dishonest.

## Known limitations

- `--exclude-standard` honours the user's `core.excludesFile`, so the set of untracked files can
  differ between machines (§8 specifies `--exclude-standard`).
- `GIT_DIR`, `GIT_WORK_TREE` and similar are inherited from the environment, because §8 says
  "inherited minus nothing".
- Blobs are read fully into memory (`cat-file blob`). There are one or two git calls per changed
  file; batching is deferred.
- No positive control is possible for pager canaries: git pages only on a TTY.
- The symlink policy for untracked files lives here. §4 gives symlink policy to `discovery` (M2+),
  which must adopt the same rule: never follow.

## Consequences

`tests/security/test_git_hardening.py` covers:
- fsmonitor, `diff.external`, textconv and repository-filter canaries;
- a partial-clone uploadpack canary;
- a submodule filter canary.

Each canary first runs every rift.git call and asserts the canary did not fire, then runs plain git
as a positive control and asserts it did. The same file covers injection through every revision
entry point, non-UTF-8 names, and a planted executable.

`tests/integration/git/*` uses real repositories for:
- renames, including staged renames in the working tree;
- deletions, type changes, a detached HEAD, a shallow clone (change detection, merge base) and a
  dirty tree;
- untracked and ignored files, nested repositories, and `rm --cached`;
- exotic names, both commit↔commit and modified in the working tree;
- attribute-independent binary detection, and the hostile-config test.
