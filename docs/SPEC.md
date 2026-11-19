# RIFT — Master Engineering & Implementation Specification

> **Spec version:** 1.1 (enhanced from v1.0). This file is the source of truth for *what* to build.
> `CLAUDE.md` governs *how* the agent works. `PROJECT_STATUS.md` records *where* we are.
> Where this spec is silent, follow §29 (ambiguity protocol) and record an ADR.

---

## 0. PROJECT IDENTITY

| Field | Value |
|---|---|
| Project | RIFT |
| Tagline | Proof-carrying changes for modern software. |
| Category | Open-source developer infrastructure |
| License | MIT |
| Language | Python ≥ 3.11 (CI: 3.11, 3.12, 3.13) |
| Architecture | Modular monolith, single distributable, `src/` layout |
| Import package | `rift` |
| CLI command | `rift` |
| PyPI distribution | `rift-cli` — **availability must be verified in M0** (record result in ADR-0003). Fallbacks: `rift-proof`, `riftcheck`. |
| Interface | CLI (Typer + Rich) |
| Storage | Local filesystem + SQLite (stdlib `sqlite3`) |
| First release | v0.1.0 |
| Change Proof Protocol | 0.1.0 |
| Plugin API | 1 |

---

## 1. PRODUCT THESIS

RIFT is a **deterministic, evidence-backed change-impact and verification engine.**

Given a code change, RIFT answers, with traceable evidence:
1. What changed? (Git)
2. What could that affect? (static analysis → dependency graph → impact)
3. Which checks are relevant? (verification planning)
4. What happened when those checks ran? (authorized execution → evidence)
5. Does the evidence satisfy policy? (PASS / FAIL / INCONCLUSIVE)
6. What don't we know? (limitations, unresolved relationships)

The output is a **Change Proof**: a portable, schema-validated JSON document recording impact and verification evidence for a change.

### 1.1 Non-goals (v0.1)

- Not an AI code generator, chatbot, or LLM wrapper. No model calls anywhere in the core.
- Not a linter wrapper.
- Not a correctness oracle. A PASS means "policy requirements were met by the collected evidence", never "the code is correct".
- Not a sandbox. RIFT does not isolate executed tests (§21).
- No languages other than Python. No runtime tracing. No automatic invariant inference. No hosted service.

---

## 2. FOUNDATIONAL PRINCIPLES (MANDATORY)

### 2.1 Deterministic core
Analysis, graph construction, impact, policy evaluation and proof generation are pure functions of their inputs. See §7 for the exact determinism contract.

### 2.2 Evidence provenance
- Every claim references ≥ 1 evidence ID.
- Never invent results. Never present an inferred edge as confirmed. Never present a selected/suggested test as executed.
- Every edge carries `origin` and `confidence` (§6.3).

### 2.3 Conservative verdicts
Unknown ≠ success. Missing mandatory evidence → INCONCLUSIVE. Mandatory violation → FAIL. PASS only when every mandatory requirement is satisfied. Full truth table in §15.

### 2.4 Local-first
Works with no cloud accounts, API keys, external DBs, hosted services, AI models, or Docker. **No network access at all** in the core; any future networked integration is opt-in and documented.

### 2.5 Extensibility through documented contracts
Plugins depend only on `rift.plugins.api`. Nothing else is public API in v0.1.

### 2.6 Security by default
- Passive commands (`analyze`, `impact`, `check --plan`, `proof *`, `config *`, `plugins *`) **never execute repository-controlled code** — including `conftest.py`, `setup.py`, git hooks, git fsmonitor, diff drivers, or plugins not on the allowlist.
- Execution requires an explicit CLI flag (`--execute`).
- Subprocesses use argument lists only. `shell=True` is banned repo-wide (enforced by Ruff `S602`/`S604`).

---

## 3. TECHNOLOGY STACK & DEPENDENCY BUDGET

### 3.1 Runtime dependencies (hard budget — adding one requires an ADR)
| Package | Purpose |
|---|---|
| `typer` | CLI definitions (brings `click`, `rich`) |
| `rich` | Terminal rendering (declare explicitly) |
| `jsonschema` | Validating proofs and config against JSON Schema |

Everything else uses stdlib: `ast`, `tokenize`, `tomllib`, `sqlite3`, `hashlib`, `subprocess`, `importlib.metadata`, `dataclasses`, `enum`, `json`, `xml.etree` (JUnit parsing — use `defusedxml`? **No**: JUnit XML is produced by our own pytest invocation into a temp file we control; still parse with entity expansion disabled via `xml.etree.ElementTree.XMLParser` defaults, which do not expand external entities. Document in ADR.)

**Pydantic: not used in v0.1.** Internal models are frozen `dataclasses` (`slots=True`). External boundaries (config, proofs, invariants) are validated by JSON Schema then mapped to dataclasses by explicit constructors. One validation mechanism, zero extra deps. (ADR-0003.)

### 3.2 Dev dependencies (`[dependency-groups] dev`)
`pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `import-linter`.

### 3.3 Optional extras (not in v0.1 implementation; reserve names only)
`rift-cli[treesitter]`, `rift-cli[mcp]`.

### 3.4 Tooling
- `uv` for env, lock (`uv.lock` committed), build (`uv build`), run.
- Build backend: `hatchling` (or `uv_build` if stable at M0 time — choose and record in ADR-0003).
- Version single-sourced in `src/rift/__init__.py` (`__version__`) or via build-backend dynamic version.

---

## 4. REPOSITORY STRUCTURE

```
rift/
├── src/rift/
│   ├── __init__.py          # __version__ only
│   ├── __main__.py          # python -m rift
│   ├── core/                # models, enums, ids, errors, canonical JSON, clock. NO internal deps.
│   │   └── models/          # one file per domain area (repo, change, graph, impact, verification, evidence, policy, proof, plugin)
│   ├── config/              # load + validate .rift/config.toml, defaults
│   ├── git/                 # hardened git subprocess layer, change detection
│   ├── discovery/           # repo root, source roots, file enumeration, exclusions, symlink policy
│   ├── cache/               # SQLite incremental cache, migrations, integrity
│   ├── graph/               # in-memory directed graph, traversal, serialization
│   ├── analysis/            # language analyzer registry; analysis/python/ = AST analyzer
│   ├── impact/              # impact computation + classification
│   ├── evidence/            # evidence construction, ID derivation, store
│   ├── invariants/          # .rift/invariants.toml loading + requirement mapping
│   ├── verification/        # test discovery, planning, execution, result parsing
│   ├── policy/              # deterministic evaluator
│   ├── proof/               # builder, validator, canonical serialization, schema loading
│   ├── plugins/             # api.py (PUBLIC contract), registry, trust/allowlist
│   ├── services/            # application orchestration (use cases). CLI calls ONLY this layer.
│   └── cli/                 # Typer app, commands, Rich renderers, exit codes
├── specs/                   # change-proof.schema.json, config.schema.json, invariants.schema.json
├── tests/{unit,integration,contract,security,property,fixtures}/
├── examples/                # runnable demo repos
├── benchmarks/              # pytest-based benchmarks; results never committed as claims
├── docs/                    # SPEC.md, architecture.md, protocol.md, plugins.md, adr/
├── .github/workflows/
└── .rift/                   # RIFT dogfooding itself (from M7)
```

`specs/` JSON Schemas are packaged into the wheel (copy into `src/rift/proof/schemas/` at build or keep canonical copy in package and symlink-free mirror in `specs/` — decide in M0, ADR).

### 4.1 Layering (enforced by import-linter in CI from M0)

Lower layers must not import higher layers. Siblings on the same layer must not import each other unless listed.

```
L9  rift.cli
L8  rift.services
L7  rift.proof
L6  rift.policy
L5  rift.verification
L4  rift.impact | rift.invariants
L3  rift.analysis
L2  rift.graph | rift.evidence | rift.cache | rift.git | rift.discovery | rift.config | rift.plugins
L1  rift.core
```

Specific rules:
- `rift.core` imports nothing from `rift`.
- `rift.plugins.api` imports only `rift.core`.
- `rift.proof` must not import `analysis`, `graph`, `impact`, `verification`, `git` (spec requirement: builder contains no analysis/execution logic). It consumes `rift.core.models` values.
- `rift.policy` must not import `cli` or any renderer.
- `rift.cli` imports only `rift.services` and `rift.core` (for types/exit codes).
- No circular imports (import-linter `independence`/`layers` contracts).

---

## 5. CODE CONVENTIONS

- `from __future__ import annotations` not needed (3.11); use modern syntax (`X | None`, `list[str]`).
- `mypy --strict` on `src/`. Tests: mypy with `disallow_untyped_defs = false`.
- Ruff: `select = ["E","F","W","I","B","UP","S","SIM","RUF","PTH","PL","N","T20","DTZ","TRY"]` (tune in M0; every ignore justified in a comment).
- Models: `@dataclass(frozen=True, slots=True, kw_only=True)`. Collections in models are `tuple[...]` or `frozenset[...]`, never `list`/`dict` (immutability + hashability). Mappings: `tuple[tuple[str, X], ...]` or a small frozen mapping type in core.
- Enums: `class X(StrEnum)` with lowercase snake values (serialized form = value).
- Errors: one base `RiftError` in `rift.core.errors` with subclasses carrying `code: ErrorCode`, `message`, `hint`, `context`. Never `except Exception: pass`. Broad catches only at plugin boundaries and the CLI top level, and they must convert to structured diagnostics.
- No global mutable state. Dependencies (clock, runner, filesystem root, config) are passed explicitly.
- No `print`. Output goes through `rift.cli` renderers. Library code uses `logging` (logger per module, no handlers configured in library code).
- Paths: `pathlib.Path` internally; **repository-relative POSIX strings** in every serialized artifact.
- No `time.sleep`. No hidden network calls.

---

## 6. CORE DATA MODELS (`rift.core.models`)

Minimum fields below; implementers may add fields, never remove these without an ADR.

### 6.1 Repository & changes
- `Repository(root: Path, vcs: "git", is_shallow: bool, default_branch: str | None)`
- `RepositoryRevision(commit: str | None, ref: str | None, is_detached: bool, is_dirty: bool)` — `commit=None` means working tree.
- `ChangeSet(base: RepositoryRevision, head: RepositoryRevision, files: tuple[ChangedFile, ...], includes_uncommitted: bool)`
- `ChangedFile(path: str, old_path: str | None, status: ChangeStatus, changed_lines: tuple[LineRange, ...], is_binary: bool)`
- `ChangeStatus`: `added | modified | deleted | renamed | copied | type_changed`
- `LineRange(start: int, end: int)` — 1-based, inclusive, refers to the **new** file (old file for deletions).

### 6.2 Symbols
- `Symbol(id: SymbolId, kind: SymbolKind, name: str, qualname: str, module: str, location: SourceLocation, visibility: Visibility, decorators: tuple[str, ...])`
- `SymbolKind`: `module | class | function | method | constant | test_function | test_method`
- `Visibility`: `public | private | dunder` (public = no leading `_`, or listed in `__all__`; `__all__` wins when present).
- `SourceLocation(path: str, start_line: int, start_col: int, end_line: int, end_col: int)`

### 6.3 Graph
- `NodeKind`: `file | module | class | function | method | constant | test | api_contract` (`constant`: a module-level constant, §9.2)
- `EdgeKind`: `imports | calls | inherits | references | tests | depends_on`
- `EdgeOrigin`: `ast_static | name_resolution | heuristic | config_declared | plugin`
- `Confidence`: `certain | likely | possible | unknown` — ordinal enum, **not** floats (floats invite fake precision and break determinism).
- `Node(id: NodeId, kind: NodeKind, label: str, location: SourceLocation | None, attributes: FrozenMap)`
- `Edge(source: NodeId, target: NodeId, kind: EdgeKind, origin: EdgeOrigin, confidence: Confidence, evidence_ids: tuple[EvidenceId, ...], flags: frozenset[EdgeFlag])`
- `EdgeFlag`: `type_checking_only | conditional | star_import | dynamic`
- `UnresolvedReference(source: NodeId, raw: str, reason: UnresolvedReason, location: SourceLocation)`
- `UnresolvedReason`: `dynamic_import | dynamic_attribute | star_import | external_module | missing_module | syntax_error | exec_eval | sys_path_mutation | module_getattr | unknown`

**Node ID grammar (stable, deterministic):**
- `file:<repo-relative-posix-path>` → `file:src/pkg/auth.py`
- `py:<dotted.module>` → `py:pkg.auth`
- `py:<dotted.module>:<qualname>` → `py:pkg.auth:Session.refresh`
- `test:<pytest-node-id>` → `test:tests/test_auth.py::TestLogin::test_ok`

### 6.4 Impact / verification / evidence / policy / proof
- `ImpactResult(changed_nodes, affected_nodes, public_interfaces, related_tests, unresolved, classification: ImpactClass, completeness: Completeness, limitations: tuple[Limitation, ...], explanations)` — `limitations` lists every reason the result is not complete (`partial`/`unknown` always has at least one), plus informational entries of documented kinds that do not affect completeness (a `complete` result carries only those)
- `Completeness`: `complete | partial | unknown`
- `VerificationPlan(mode: PlanMode, selected: tuple[PlannedCheck, ...], excluded: tuple[ExclusionRecord, ...], full_suite_recommended: bool, reasons: tuple[str, ...])`
- `PlanMode`: `selective | full_suite`
- `VerificationRun(plan_id, command: tuple[str, ...], exit_code, started_at, finished_at, duration_ms, tool: ToolInfo, results: tuple[TestResult, ...], timed_out: bool)`
- `TestResult(node_id, outcome: TestOutcome, duration_ms, message: str | None)` — `TestOutcome`: `passed | failed | error | skipped | xfailed | xpassed | not_run`
- `Evidence` — §13. `Claim(id, statement_kind: ClaimKind, subject, evidence_ids, status: ClaimStatus)`
- `Invariant`, `Requirement`, `Policy`, `RequirementResult`, `PolicyResult` — §14–15.
- `ChangeProof` — §16.
- `PluginMetadata(name, version, api_version, kind: PluginKind, capabilities, file_patterns, requirements)`

All models implement explicit `to_dict()` / `from_dict()` in a dedicated `rift.core.serialization` module (not via reflection magic), producing JSON-native types only.

---

## 7. DETERMINISM CONTRACT

1. **Canonical JSON**: UTF-8, `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, no NaN/Infinity (`allow_nan=False`), integers only for counts/durations (ms). Human output may pretty-print; hashing always uses canonical form.
2. **Ordering**: every collection in output is sorted by a documented key (nodes by id; edges by `(source, kind, target)`; files by path; evidence by id). **Never iterate a `set` to produce output** — Python string hashing is randomized per process.
3. **Identifiers** are content-derived: `<prefix>_<first 24 hex of sha256(canonical_json(payload))>`. Prefixes: `ev_` evidence, `cl_` claim, `pl_` plan, `rp_` proof. Payload for each excludes volatile fields.
4. **Volatile fields** (excluded from IDs and from equivalence comparison): `generated_at`, `collected_at`, `started_at`, `finished_at`, `duration_ms`, `environment.hostname`. Hostname is opt-in only (default omitted).
5. **Clock** is injected (`rift.core.clock.Clock` protocol). All timestamps: UTC, RFC 3339, `Z` suffix, second precision.
6. **Equivalence**: two runs on the same repo state + config + tool versions produce proofs that are byte-identical after removing volatile fields. This is a contract test (`tests/contract/test_determinism.py`), run with different `PYTHONHASHSEED` values.
7. No absolute paths, usernames, or environment-variable values in any proof.

---

## 8. GIT ENGINE (`rift.git`)

All git calls go through one function, `run_git(args: Sequence[str], *, cwd: Path, timeout: float) -> GitResult`, which:

- Executes `["git", *HARDENING_ARGS, *args]` with `shell=False`.
- `HARDENING_ARGS = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "diff.external=", "-c", "core.pager=cat", "-c", "color.ui=false"]`.
- Diff commands always add `--no-ext-diff --no-textconv --no-color`.
- Env: inherited minus nothing, plus `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `LC_ALL=C`, `GIT_PAGER=cat`.
- Uses `-z` (NUL-delimited) output for any command returning paths. Never parse quoted paths.
- Revision arguments from users: reject values starting with `-`; validate via `git rev-parse --verify --end-of-options <rev>^{commit}`. Pathspecs always after `--`.
- Non-zero exit → `GitError(code, stderr_tail, hint)`. Never raise raw `CalledProcessError` out of the module.
- `git` missing / too old (< 2.30 — confirm minimum in M1) → `GitUnavailableError` surfaced by `rift doctor`.
- `safe.directory` refusal → structured error with remediation hint; RIFT never edits git config.

Features: repo discovery (`rev-parse --show-toplevel`), current revision, detached HEAD, shallow detection (`rev-parse --is-shallow-repository`), base resolution (`--base` → explicit rev; default: config `git.default_base`, else `merge-base HEAD origin/HEAD` if available, else error with hint), commit↔commit and commit↔working-tree diffs (`diff --name-status -z -M`, `diff --unified=0` for line ranges), untracked files (`ls-files --others --exclude-standard -z`), rename detection (`-M`, similarity threshold configurable, default 50%).

Shallow clone + unreachable base → `ShallowHistoryError` with hint (`git fetch --deepen=N` / `fetch-depth: 0` in Actions).

---

## 9. PYTHON ANALYZER (`rift.analysis.python`)

Pure static analysis with stdlib `ast`. **Never imports or executes analyzed code.**

### 9.1 Input handling
- Read bytes; enforce `analysis.max_file_bytes` (default 1 MiB) → over limit: skip + `Limitation(oversized_file)`.
- Decode with `tokenize.detect_encoding`; failure → diagnostic, skip.
- `ast.parse(..., type_comments=False)`; catch `SyntaxError`, `ValueError` (null bytes), `RecursionError`, `MemoryError` → `Diagnostic(severity=error)` + `UnresolvedReason.syntax_error`. Analysis of other files continues.
- Syntax newer than the running interpreter is reported as such (record analyzer interpreter version in evidence).

### 9.2 Extraction
Modules, imports (absolute, relative, aliased, `from x import *`), functions, async functions, classes, methods, decorators (as dotted strings), base classes, module-level constants (`UPPER_CASE` assignments), `__all__` (literal lists/tuples only; non-literal → limitation), call sites (callee as dotted name where statically resolvable), `if TYPE_CHECKING:` imports (flag `type_checking_only`), `try/except ImportError` imports (flag `conditional`).

### 9.3 Resolution
- Module names derived from configured source roots (default autodetect: `src/` if it contains packages, else repo root). Namespace packages (no `__init__.py`) supported.
- Relative imports resolved against the importing module's package.
- Imports resolving outside source roots → `UnresolvedReference(reason=external_module)`; not graph nodes in v0.1.
- Call resolution: `certain` for direct calls to imported/defined names; `likely` for `self.method()` within the defining class hierarchy; `possible` for attribute calls matched by name only; otherwise unresolved.

### 9.4 Dynamic behavior — must be recorded, never guessed
`importlib.import_module(non_literal)`, `__import__`, `getattr/setattr` with non-literal names, `exec`/`eval`/`compile`, star imports, `sys.path` mutation, module-level `__getattr__`, monkeypatching (assignment to attributes of imported modules), `globals()`/`locals()` writes. Literal-argument forms of `import_module("x.y")` resolve with `confidence=likely`, `flag=dynamic`.

### 9.5 Output
`AnalysisResult(file_path, content_sha256, analyzer_name, analyzer_version, symbols, edges, unresolved, diagnostics, limitations)` — serializable, cacheable, deterministic.

---

## 10. DEPENDENCY GRAPH (`rift.graph`)

Custom in-memory directed multigraph. No graph DB, no networkx.

- Adjacency stored in both directions: `dict[NodeId, dict[EdgeKind, dict[NodeId, Edge]]]` (dict preserves insertion order; outputs are still sorted).
- API: `add_node`, `add_edge` (target must exist or be rejected → caller records unresolved), `successors`, `predecessors`, `reverse_reachable(start, kinds, min_confidence, max_depth=None)`, `forward_reachable(...)`, `subgraph(nodes)`, `to_dict()` / `from_dict()`.
- Traversal: iterative BFS (no recursion — deep graphs must not hit the recursion limit), visited set, returns a sorted tuple plus the shortest path to each reached node (for explanations).
- Duplicate edge (same source, kind, target): keep the strongest confidence; merge evidence IDs.
- The restricting flags (`type_checking_only`, `conditional`) are part of that key: edges that differ only in them are kept as parallel edges, and traversal explains a hop with the strongest of them.
- Property tests (Hypothesis): reachability is transitive; reverse(forward) symmetry; serialization round-trips; output identical across insertion orders.

---

## 11. IMPACT ENGINE (`rift.impact`)

Algorithm (documented in `docs/architecture.md`):
1. Map each `ChangedFile` + changed line ranges to nodes: symbols whose span intersects changed lines; module node always; deleted file → its previously cached nodes (or file node only, marked `completeness=partial` if no cache).
2. Reverse traversal over `imports | calls | inherits | references | depends_on`, respecting `impact.min_confidence` (default `possible`) and `impact.max_depth` (default unlimited).
3. Collect public interfaces among changed + affected nodes.
4. Collect tests: nodes of kind `test` reachable via reverse traversal, plus `tests` edges.
5. Collect unresolved references touching any changed/affected node.
6. Classify (below). 7. Emit `ImpactResult` with per-node explanation path.

### 11.1 Classification (highest matching tier wins; ordinal, **not a risk probability**)
| Tier | Rule |
|---|---|
| `none` | No changed file maps to an analyzable node (docs, non-source files outside config). |
| `local` | All changed symbols are private and have no reverse dependents outside their own module. |
| `module` | Reverse dependents exist, all within the same top-level package. |
| `cross_package` | Reverse dependents in ≥ 2 top-level packages. |
| `public_api` | A changed or deleted symbol is public in a module of a package listed in `project.public_packages` (default: all top-level packages under source roots). |

`completeness` is orthogonal: `partial` if any unresolved reference touches the impact set, any analyzed file failed to parse, or a deleted file had no cached analysis; `unknown` if analysis could not run.

---

## 12. VERIFICATION ENGINE (`rift.verification`)

Four separate stages, separate modules: **discovery → planning → execution → result collection**.

### 12.1 Discovery
- **Static discovery (passive, default):** read pytest config statically — `pyproject.toml [tool.pytest.ini_options]`, `pytest.ini`, `setup.cfg [tool:pytest]`, `tox.ini [pytest]` via `tomllib`/`configparser` — for `testpaths`, `python_files`, `python_classes`, `python_functions`. Use the AST analyzer to find test functions/methods and build approximate node IDs. Parametrization, fixtures-generated tests and plugin-generated tests are **not** visible → recorded limitation.
- **Collection (execution-class, requires `--execute`):** `python -m pytest --collect-only -q` imports `conftest.py` and test modules, i.e. runs repository code. Therefore it is treated exactly like test execution and never runs under passive commands.

### 12.2 Planning (`rift check --plan`, passive)
- Selected = tests in `ImpactResult.related_tests`.
- **Full suite recommended** (with explicit reason) when any of: a `conftest.py` changed; pytest/packaging config changed (`pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`, `requirements*.txt`, `uv.lock`); impact `completeness != complete` and `verification.full_suite_on_partial = true` (default true); a dynamic/unresolved reference touches the impact set; no tests could be linked but source files changed; non-Python changed files under source roots.
- Every discovered test not selected appears in `excluded` with a reason. **Nothing is silently omitted.**

### 12.3 Execution (`rift check --execute`)
- Command constructed as a list: `[python, "-m", "pytest", "-p", "no:cacheprovider", "--junitxml", <tmpfile>, "-q", *node_ids]`.
- `python` = `verification.python` from config, else the interpreter of an activated venv (`VIRTUAL_ENV`) under the repo, else `sys.executable` — resolution recorded in evidence; `rift doctor` warns if pytest is not importable there.
- Node IDs validated: must be repo-relative, no `..`, no leading `-`. If more than `verification.max_node_ids` (default 500), run by file paths and record the coarsening.
- Timeout: `verification.timeout_seconds` (default 900). Start in a new process group/session; on timeout kill the group, mark `timed_out=true`, outcomes of unfinished tests = `not_run`.
- Environment inherited (tests need it). **Env values are never recorded.**
- Results from JUnit XML (pytest built-in; no plugin dependency). If the XML is missing/corrupt, all planned tests → `not_run` and the run is an `error` evidence record — never assumed passed.
- Capture: command, exit code, node IDs, per-test outcome, duration, timestamps, pytest version (from JUnit/`--version`), interpreter version.

### 12.4 Pytest exit codes mapping
0 all passed · 1 some failed · 2 interrupted · 3 internal error · 4 usage error · 5 no tests collected. Codes 2–5 produce evidence with `result=error`; 5 with a non-empty selection is a mandatory-evidence gap, not a pass.

---

## 13. EVIDENCE ENGINE (`rift.evidence`)

`Evidence(id, type: EvidenceType, producer, producer_version, subject, result: EvidenceResult, source_location | None, collected_at, confidence: Confidence, epistemic: Epistemic, limitations: tuple[str, ...], data: FrozenMap)`

- `EvidenceType`: `git_change | static_analysis | graph_edge | impact_analysis | test_discovery | test_execution | invariant_check | config | environment`
- `EvidenceResult`: `pass | fail | error | info | unknown`
- `Epistemic`: `fact | inference | unknown` — the explicit facts/inferences/unknowns separation. Executed test outcomes and git diffs are `fact`; graph edges with confidence < `certain` are `inference`; unresolved references are `unknown`.
- ID = `ev_` + hash of canonical payload excluding `id` and `collected_at`.
- `data` must be JSON-native and schema-described per type (in `specs/change-proof.schema.json` `$defs`).

---

## 14. INVARIANT ENGINE (`rift.invariants`)

Manual invariants only in v0.1. File: `.rift/invariants.toml`, validated by `specs/invariants.schema.json`.

```toml
schema_version = "0.1.0"

[[invariant]]
id = "auth-tests-pass"                 # ^[a-z0-9][a-z0-9-]{1,63}$, unique
description = "Auth module changes must have passing auth tests"
scope = { paths = ["src/app/auth/**"] } # glob; or symbols = ["py:app.auth:*"]
level = "mandatory"                     # mandatory | optional
requires = [
  { kind = "tests_passed", select = { paths = ["tests/auth/**"] } },
]
```

`requires.kind` in v0.1: `tests_passed`, `tests_executed`, `analysis_complete`, `no_public_api_change`. Unknown kinds → requirement status `unsupported` (reported, never silently dropped). Architecture keeps a `RequirementChecker` protocol so static/runtime adapters slot in later.

---

## 15. POLICY ENGINE (`rift.policy`)

Inputs: `Policy` (from config + invariants in scope), evidence set. Output: `PolicyResult(verdict, requirement_results, summary)`.

Each requirement evaluates to one `RequirementStatus`: `satisfied | violated | missing | unsupported | not_applicable`.

### 15.1 Verdict rule (exact)
```
mandatory = [r for r in results if r.level == mandatory and r.status != not_applicable]
if any(r.status == violated for r in mandatory):            FAIL
elif any(r.status in {missing, unsupported} for r in mandatory): INCONCLUSIVE
else:                                                        PASS
```
Optional requirements never change the verdict; they are reported.

### 15.2 Default policy (when the user has configured nothing)
Mandatory: `analysis_completed` (no fatal analysis error), `selected_tests_executed`, `selected_tests_passed`. Configurable `policy.require_complete_analysis` (default **false** in v0.1; when true, partial completeness → `missing`).

Consequence (intended): `rift check` without `--execute` yields **INCONCLUSIVE**, because test evidence is missing.

### 15.3 Required property tests (Hypothesis)
- Monotonicity: changing any mandatory result to `violated` never improves the verdict; turning `missing` into `satisfied` never worsens it.
- Optional results never change the verdict.
- Empty mandatory set → PASS (document this explicitly; default policy is never empty).
- Evaluation is order-independent.

---

## 16. CHANGE PROOF PROTOCOL 0.1.0 (`rift.proof`, `specs/change-proof.schema.json`)

JSON Schema draft 2020-12. `$id`: `https://rift.dev/schemas/change-proof/0.1.0` (placeholder domain — do not imply a hosted service exists; mark in docs).

Top-level required fields:
| Field | Content |
|---|---|
| `schema_version` | `"0.1.0"` |
| `proof_id` | `rp_…` content-derived (§7) |
| `generated_at` | RFC 3339 UTC (volatile) |
| `subject` | repo identity (remote URL **with credentials stripped**, or `null`), base/head revisions, dirty flag |
| `environment` | rift version, python version, platform (`sys.platform`), git version, analyzer & adapter versions, config hash |
| `changes` | changed files (status, paths, line ranges) |
| `claims` | `[{id, kind, subject, evidence_ids[], status}]` — every `evidence_ids` entry must resolve |
| `evidence` | evidence records (§13) |
| `verification` | plan (selected + excluded with reasons) and runs |
| `limitations` | analysis limitations, unresolved references summary, coverage gaps |
| `policy` | policy identity/hash and per-requirement results |
| `verdict` | `pass | fail | inconclusive` |

Rules:
- **Referential integrity** (validator, beyond schema): every claim evidence ID exists; every evidence ID unique; verdict equals re-evaluation of `policy.requirement_results` with §15.1.
- **Extensions**: any object may carry keys matching `^x-[a-z0-9-]+$`; validators accept and builders/readers **preserve** them round-trip. Other unknown keys → validation error.
- **Versioning**: SemVer. Patch = clarifications; minor = additive optional fields; major = breaking. Consumers must reject unknown major versions. Any schema change requires an ADR + CHANGELOG entry + protocol doc update (no silent protocol changes).
- `ProofBuilder` takes finished model values only; it contains no analysis, graph traversal, or execution. `ProofValidator` = schema + referential + verdict-consistency checks, usable standalone on any file.
- Storage: `.rift/proofs/<proof_id>.json` (canonical JSON). `rift proof show` reads the latest by `generated_at` recorded in an index file, not by mtime.

---

## 17. SQLITE CACHE (`rift.cache`)

- Location: `.rift/cache/rift.db` (configurable). Disposable; proofs never stored here.
- Schema version via `PRAGMA user_version`; forward-only migrations in code; unknown newer version → discard and rebuild (with notice).
- Tables (minimum): `files(path PK, size, mtime_ns, sha256)`, `analysis(path, sha256, analyzer, analyzer_version, config_hash, payload_json, PK(path, analyzer))`, `meta(key PK, value)`.
- Validity key: `(sha256, analyzer_version, config_hash, rift_version)`. `(size, mtime_ns)` is only a fast-path hint; **before an entry contributes to a proof, its sha256 is re-verified against file content.**
- On open: `PRAGMA quick_check`; failure or `sqlite3.DatabaseError` → rename to `rift.db.corrupt-<ts>`, rebuild, emit a diagnostic. Never crash, never trust.
- Deleted files are purged; renamed files re-keyed by content hash.
- Single-writer: use a lock file; concurrent `rift` processes wait with bounded timeout or proceed cache-less.
- `rift analyze --no-cache` bypasses entirely; contract test asserts cached vs uncached proofs are equivalent.

---

## 18. PLUGIN SYSTEM (`rift.plugins`)

- Public contract: `rift.plugins.api` only — `typing.Protocol`s `LanguageAnalyzer`, `VerificationAdapter`, `EvidenceProvider`, `Integration`, plus re-exported model types they need, and `PLUGIN_API_VERSION = 1`.
- Discovery: `importlib.metadata.entry_points(group="rift.plugins")` — reading entry-point metadata does **not** import plugin code.
- **Trust**: a third-party plugin is loaded only if listed in `[plugins] allow = ["name"]` (optionally `"name==1.2.3"`). Discovered-but-not-allowed plugins are listed by `rift plugins list` as `blocked`. Built-in plugins (Python analyzer, pytest adapter) are registered through the same interface and always trusted.
- Compatibility: `PluginMetadata.api_version` must equal `PLUGIN_API_VERSION` major; otherwise not loaded, reported.
- Isolation: plugin calls wrapped; any exception → diagnostic + limitation, other analysis continues. Plugins return data; they cannot write proofs, cache, or config. (This is fault isolation, not a security sandbox — allowed plugins run with full user privileges; document this.)

---

## 19. CLI (`rift.cli`)

Commands (all thin; each calls exactly one service function):

```
rift init                          # create .rift/{config.toml,invariants.toml,.gitignore}; idempotent; never overwrites without --force
rift doctor                        # environment checks: python, git version, repo, shallow, pytest importable in target interpreter, cache health, plugins
rift analyze [--base REV] [--json] [--no-cache]
rift impact PATH... | --symbol NAME [--json]
rift check [--plan] [--execute [--full-suite]] [--base REV] [--json]
rift proof show [PROOF_ID] [--json]
rift proof validate [FILE]
rift proof export [PROOF_ID] --format json [--output FILE]
rift plugins list
rift plugins inspect NAME
rift config show [--json]
rift config validate
rift version [--json]
```

`rift check` without flags ≡ `--plan` + policy evaluation on available evidence + proof generation (→ INCONCLUSIVE by default policy).

Global: `--repo PATH`, `--config PATH`, `--verbose/-v`, `--quiet/-q`, `--no-color` (also honor `NO_COLOR`).

### 19.1 Streams
- `--json`: **stdout contains exactly one JSON document**, nothing else. All logs, progress, warnings → stderr.
- Human mode: results → stdout, diagnostics/progress → stderr. Rich auto-disables styling when not a TTY.

### 19.2 Exit codes (stable, documented, contract-tested)
| Code | Meaning |
|---|---|
| 0 | Success / verdict PASS |
| 1 | Verdict FAIL |
| 2 | CLI usage error (Click default) |
| 3 | Verdict INCONCLUSIVE |
| 4 | Configuration invalid |
| 5 | Repository / git error |
| 6 | Proof invalid (`proof validate`) |
| 7 | Execution environment error (pytest not found, timeout) |
| 70 | Internal error (bug) — message asks user to file an issue |
| 130 | Interrupted |

---

## 20. CONFIGURATION (`.rift/config.toml`, `specs/config.schema.json`)

```toml
schema_version = "0.1.0"

[project]
name = "my-app"
source_roots = ["src"]           # default: autodetect
public_packages = []             # default: all top-level packages
exclude = ["**/migrations/**", "build/**", ".venv/**"]

[git]
default_base = "main"
rename_similarity = 50

[analysis]
languages = ["python"]
max_file_bytes = 1048576
follow_symlinks = false

[impact]
min_confidence = "possible"

[verification]
framework = "pytest"
python = ""                      # "" = auto-resolve (§12.3)
timeout_seconds = 900
full_suite_on_partial = true
max_node_ids = 500

[cache]
enabled = true
path = ".rift/cache/rift.db"

[policy]
require_complete_analysis = false

[plugins]
allow = []
```

All keys optional; defaults live in code (`rift.config.defaults`) and are emitted by `rift init`. Unknown keys → error with "did you mean" suggestion. Validation errors name the file, key path, and expected value.

---

## 21. SECURITY

### 21.1 Threat model (documented in `SECURITY.md`)
Adversary controls the analyzed repository's contents (files, names, git config inside `.git/` is *not* attacker-controlled for clones, but `.gitattributes`, filenames, symlinks, source, test files, and pyproject are). RIFT must not execute that content under passive commands, must not escape the repo root, and must not leak secrets into proofs.

### 21.2 Required controls and tests (`tests/security/`)
| Threat | Control | Test |
|---|---|---|
| Path traversal | All paths resolved and checked `is_relative_to(repo_root)`; reject `..` in user input | `../../etc/passwd` args, crafted diff paths |
| Malicious filenames | `-z` git output; no shell; names with newlines, leading `-`, unicode, spaces | fixture repo with such names |
| Command injection | argv lists only; Ruff S602/S604; `--end-of-options` | revision `--output=/tmp/x`, `;rm -rf` |
| Passive code execution | analyzer never imports; no collection without `--execute`; git hardening | canary `conftest.py`/module that writes a file on import — assert not written after `analyze`/`check --plan` |
| Untrusted plugins | allowlist | entry point not in allowlist is never imported (canary) |
| Symlinks | not followed by default; links pointing outside root always rejected | symlink to `/` and to sibling dir |
| Cache corruption | quick_check + rebuild | truncated/garbage db file |
| Invalid proof input | schema + referential validation, size cap (default 50 MiB) | malformed, huge, deeply nested JSON |
| Oversized source | `max_file_bytes` | 5 MiB file, deeply nested expression (RecursionError) |
| Malformed config | schema validation | wrong types, unknown keys, invalid TOML |
| Secret leakage | strip URL credentials; never record env values | remote `https://user:token@host/repo` |

Timeouts on every subprocess. POSIX `resource` limits optional and only claimed where tested. **Never describe execution as sandboxed.**

---

## 22. TESTING

- Layout: `tests/unit`, `tests/integration` (real git repos built in `tmp_path` via fixtures — real `git` CLI, no mocks of git), `tests/contract` (CLI exit codes, JSON output shape, schema, determinism, plugin API), `tests/security`, `tests/property` (Hypothesis), `tests/fixtures` (fixture repo *builders*, not committed `.git` dirs).
- Markers: `slow`, `integration`, `security`. Default run includes all; `-m "not slow"` for fast loop.
- Hypothesis profiles: `ci` (more examples, `derandomize=True`), `dev` (fast). Selected via `HYPOTHESIS_PROFILE`.
- Coverage: branch coverage on; `fail_under` = 85 from M1 onward (M0: reported only). Never lower it to pass.
- Must cover: every verdict, stale cache, incomplete analysis, dynamic imports, deleted/renamed files, malformed source, failed tests, timed-out tests, missing JUnit XML, shallow clone, detached HEAD, dirty tree.
- Never delete, skip, or weaken a test to go green. A known failure is marked `xfail(strict=True, reason=<issue/ADR>)` and listed in PROJECT_STATUS.md.

---

## 23. QUALITY GATE (exact commands; all must pass before any milestone is reported complete)

```bash
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
uv build
```

CI (`.github/workflows/ci.yml`): matrix `ubuntu-latest, macos-latest, windows-latest` × `3.11, 3.12, 3.13`; `permissions: contents: read`; `astral-sh/setup-uv`; same commands; wheel smoke test (install built wheel into a fresh venv, run `rift version`). Pin third-party actions to a full commit SHA with a version comment. From M7: a `dogfood` job runs `rift check --plan --json` on RIFT itself and validates the proof.

---

## 24. MILESTONES & ACCEPTANCE CRITERIA

Every milestone: design note → smallest vertical slice → tests → gate (§23) → docs → architecture self-review against §4.1/§5/§7 → PROJECT_STATUS.md update → completion report (template in CLAUDE.md). **Stop and wait for review after each milestone.**

**M0 — Foundation**
- `uv sync` from clean checkout works; `uv.lock` committed.
- Package installs; `rift --help` and `rift version` work (only CLI surface allowed in M0).
- `uv build` produces sdist + wheel; wheel installs in a fresh venv and `rift version` prints the version.
- All §4 subpackages exist with `__init__.py` whose docstring states the module's single responsibility. No feature logic.
- Ruff, mypy strict, import-linter (layers from §4.1), pytest all configured and green. Tests: version smoke, CLI help, import of every subpackage, layering contract.
- CI workflow present (green status can't be verified until pushed — report as unverified).
- LICENSE (MIT), README (honest pre-alpha status), CONTRIBUTING, PROJECT_STATUS, `docs/adr/0000-template.md`, ADR-0001 (record ADRs), ADR-0002 (modular monolith + layering), ADR-0003 (dependency budget, build backend, PyPI name check result).
- SECURITY.md stub with private reporting instructions; CODE_OF_CONDUCT (Contributor Covenant 2.1, reference by link + contact placeholder); `.gitignore`; `.editorconfig`.

**M1 — Core models & Git**: all §6 models + serialization round-trip property tests; canonical JSON + ID derivation; Clock; error hierarchy; full §8 git layer with integration tests for every listed feature, including shallow/detached/dirty/renames/malicious filenames.

**M2 — Python analyzer**: §9 complete; fixture repos covering every dynamic behavior in §9.4; syntax-error and oversized-file handling; determinism test.

**M3 — Graph**: §10 complete with Hypothesis properties; build graph from M2 output; deterministic serialization.

**M4 — Impact**: §11 incl. classification table tests per tier and completeness; `--symbol` resolution (ambiguous name → list candidates, exit 2).

**M5 — Verification**: static discovery, planning with every full-suite trigger tested, authorized execution with real pytest against fixture repos (pass, fail, error, skip, timeout, missing XML, exit code 5), canary tests proving passive commands never execute repo code. Also M5 includes cache (§17) if not landed earlier — decide in M2 design note.

**M6 — Change Proof**: schema, builder, validator (schema + referential + verdict consistency), evidence engine, policy engine with §15 property tests, invariants loader; extension-field round-trip; determinism contract test with varied `PYTHONHASHSEED`.

**M7 — CLI integration**: every §19 command, exit-code contract tests, `--json` purity tests, `rift init` idempotency, dogfood CI job.

**M8 — Plugins**: §18 complete; built-ins migrated onto the public API; example third-party plugin in `examples/` with contract tests; allowlist canary tests.

**M9 — v0.1.0 release**: Definition of Done (§25) audit with evidence for each item; CHANGELOG; docs complete; release workflow (build + PyPI trusted publishing, manual approval gate); realistic demo in `examples/`.

---

## 25. DEFINITION OF DONE (v0.1.0)

Each item must be demonstrated by a test or a recorded command output, not asserted:
installs in a clean env · works offline · analyzes Python repos · builds graphs · identifies affected components · discovers pytest tests · executes tests only with `--execute` · collects real evidence · produces schema-valid proofs · PASS/FAIL/INCONCLUSIVE correct · terminal + JSON output · comprehensive tests · GitHub Actions integration (workflow + documented usage example) · docs · realistic demo · analyzes the RIFT repo itself.

---

## 26. OPEN-SOURCE STANDARDS

README (sections: what, problem, how it works, install, quick start, example output — **captured from a real run, not invented**, architecture, Change Proof protocol, plugin development, roadmap, contributing, license), LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, GOVERNANCE (BDFL-style for now, stated plainly), CHANGELOG (Keep a Changelog), `.github/ISSUE_TEMPLATE/{bug,feature}.yml`, `.github/pull_request_template.md`, workflows, `docs/architecture.md`, `docs/adr/`, `docs/protocol.md`, `docs/plugins.md`.

Never claim features, integrations, benchmarks, adoption, or performance that are not implemented and measured. Unbuilt items go in the roadmap, labeled as planned.

---

## 27. PROJECT_STATUS.md FORMAT

```markdown
# Project Status
_Last updated: YYYY-MM-DD — by: <agent/session or human>_

## Current milestone
M<n> — <name> — <in progress | awaiting review | accepted>

## Completed (verified)
- <feature> — verified by <test path / command>

## Pending
## Known limitations
## Failing / xfail tests
## Architectural decisions (ADR index)
## Next tasks
```

"Completed" requires a verifying test or command. File existence is not completion.

---

## 28. AGENT RULES

Never: generate the whole project in one pass · implement future milestones · add infrastructure or dependencies outside §3 without an ADR · fake or paraphrase test output · replace real integrations (git, pytest, sqlite) with mocks while claiming functionality (mocks allowed in unit tests of *callers*, labeled as such) · cross layer boundaries · remove working functionality without justification · change the protocol or schemas silently · claim completion without running §23 · invent benchmarks · commit secrets.

When a requirement cannot be met: stop, report the blocker with evidence, propose options.

## 29. AMBIGUITY PROTOCOL

1. Read relevant interfaces and existing ADRs.
2. Choose the smallest defensible implementation consistent with §2.
3. Record an ADR (`docs/adr/NNNN-title.md`, status `accepted` or `proposed`).
4. Add tests that pin the decision.
5. Mention it in the milestone report under "Decisions".

## 30. KNOWN OPEN QUESTIONS (resolve via ADR when reached)

- Build backend: `hatchling` vs `uv_build` (M0).
- Final PyPI name (M0).
- Schema packaging: single canonical copy inside the package vs `specs/` mirror with sync test (M0).
- Minimum supported git version (M1).
- Whether cache lands in M2 or M5 (M2 design note).
- Windows process-group kill semantics for timeouts (M5).
- Hostname/user inclusion in environment block — default off (M6).

## 31. ROADMAP (planned, not implemented)

Planned additions. They are not part of any milestone's acceptance criteria until the
milestone starts, and none of them exists yet.

- **M7**: `rift proof export --format html`. A single, self-contained, offline HTML report of a
  Change Proof: verdict, changes, impact, tests, evidence, limitations. No server, no external
  assets, no JavaScript from a CDN.
- **M8**: a GitHub Action that posts a proof summary as a pull-request comment.
- **Post-v0.1**: a web dashboard that reads proof JSON, as a separate project. It is not part of
  this repository.
