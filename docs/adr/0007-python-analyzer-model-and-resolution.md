# ADR-0007: Python analyzer: output model, resolution conventions, cache placement

- **Status:** accepted
- **Date:** 2026-11-18
- **Milestone:** M2
- **Spec refs:** §6.2, §6.3, §7, §9, §17, §21, §24, §30

## Context

§9 defines what the analyzer extracts and how confident a resolution is. It leaves open:
- the concrete output types;
- which node an edge starts at;
- how to record dynamic behaviors that have no `UnresolvedReason` of their own;
- how to recognise "syntax newer than the interpreter";
- whether the SQLite cache (§17) lands in M2 or M5 (§30).

## Decision

### Output model (`rift.core.models.analysis`, `rift.core.models.diagnostic`)

1. `AnalysisResult` has the §9.5 fields plus two more:
   - `module`: the dotted name, or `None` when the file has no importable name.
   - `python_version`: the analyzer interpreter's `major.minor`. §9.1 says to record it, because
     parse results depend on it.
2. Every collection is stored sorted by a public key and without duplicates. The model checks
   this when constructed.
   - symbols: `id`
   - edges: `(source, kind, target, origin, confidence, flags)`
   - unresolved: `(location, reason, raw, source)`
   - diagnostics: `(location, code, severity, message, subject)`
   - limitations: `(kind, subject, message)`
3. Every edge and unresolved source is a node this file defines: its module node, its `file:`
   node, or one of its symbols. A file without a module name has no symbols, and its edges start
   at its `file:` node.
4. `Diagnostic(severity, code, message, subject, location)`. `code` is a stable snake_case
   string owned by the producer. Messages never contain absolute paths, `strerror` text or
   environment values: OS errors are reported by errno name only.
5. `Edge.evidence_ids` is empty in M2. The evidence engine is M6.

### Edges

6. **Imports** start at the importing module's node, even when the import sits inside a
   function. That is the conservative choice: the whole module depends on the import.
   - Ancestor packages that have an `__init__.py` get edges too, because importing `a.b.c` runs
     `a/__init__.py` and `a/b/__init__.py`. A package never gets an edge to itself.
   - Namespace packages have no file, so they get no node and no edge. They still resolve, and
     attribute chains through them reach real modules.
7. **Calls, references and inherits** start at the innermost enclosing definition: a function,
   a method, a class (for class-body code) or the module.
   - Decorators are `references` edges from the decorated symbol.
   - Class bases are `inherits` edges.
   - Any other name or attribute use that resolves to a project node is a `references` edge.
8. **Confidence**:
   - `certain`: a name bound to a project definition or import, including re-export chains,
     followed with cycle detection and a depth cap of 32.
   - `likely`: `self.`, `cls.` and `super()` members; members inherited from a project base
     class; instances of project classes (`Engine().go()`); a name with several candidate
     bindings (conditional redefinition); names from star imports (flag `star_import`); literal
     `import_module("x.y")` (flag `dynamic`).
   - `possible`: an attribute call whose receiver is unknown, matched by name against every
     project method (origin `heuristic`).
9. **Not recorded per call site**: calls on external modules, builtins and attributes of
   builtins. The import already records the external module. A name-match over project methods
   is complete for statically knowable project targets, so dropping a call whose name matches no
   project method loses nothing static.
10. **Flags**:
    - `conditional` marks imports in `try` blocks that catch `ImportError`,
      `ModuleNotFoundError`, `Exception` or `BaseException` (or have a bare `except`), plus imports
      under `if`/`else`.
    - `type_checking_only` marks imports under `if TYPE_CHECKING:` or `if <x>.TYPE_CHECKING:`.

### Unresolved references and §9.4 mapping

11. External top-level package → `external_module`. Project top-level package but missing
    module → `missing_module`.
12. A relative import that goes beyond the top-level package → `missing_module`, plus a
    `relative_import_beyond_top_level` warning diagnostic.
13. `from m import n` where `n` is not bound in `m`:
    - `module_getattr` if `m` defines `__getattr__`;
    - `star_import` if `m` star-imports names that cannot be listed;
    - else `missing_module`.
14. §9.4 behaviors without a dedicated reason are recorded as `dynamic_attribute`, with the
    source text in `raw`:
    - **monkeypatching**: assigning or deleting an attribute of a module, or `setattr`/`delattr`
      with a literal name on a module;
    - **namespace writes**: `globals()`, `locals()` or `vars()` written through a subscript or a
      mutating method.

    Adding enum values would be a protocol change, so this mapping keeps the vocabulary
    unchanged.
15. Other dynamic APIs:
    - `runpy.*`, `importlib.reload`, `importlib.util.spec_from_file_location` and similar
      loaders → `dynamic_import`.
    - `site.addsitedir` → `sys_path_mutation`.
16. A star import whose names cannot be listed is recorded as `star_import` (raw `m.*`). That
    covers an external module, a non-literal `__all__`, a nested star import, or an unparsed
    file. A name bound nowhere visible is `star_import` if such a star import could have bound it,
    else `unknown`.
17. `raw` is the source text of the node, with whitespace collapsed and a cap of 160 characters.
    It is independent of the interpreter version, unlike `ast.unparse`.

### Symbols

18. A redefinition merges into one symbol whose span covers every definition. Examples: a
    property setter, or a `try`/`except` fallback `def`. Over-reporting impact is conservative.
19. A symbol's span includes its decorators.
20. Test symbols follow pytest's default naming, statically:
    - files: `test_*.py`, `*_test.py`;
    - functions: `test*`;
    - classes: `Test*`.
21. Names that are not ASCII identifiers cannot form a node ID (ADR-0005), so they become an
    `unrepresentable_symbol` limitation.
22. A non-literal `__all__` (anything except a literal list/tuple of strings, `+=`, `.extend` or
    `.append` of literals) becomes a `non_literal_all` limitation. Visibility then falls back to
    the leading-underscore rule.

### Input handling (§9.1)

23. Size is checked while streaming the file. The sha256 always covers all of the bytes. An
    oversized file gets an `oversized_file` limitation and no diagnostic.
24. Decoding uses `tokenize.detect_encoding` (BOM, PEP 263 cookie).
25. Parsing uses `ast.parse` with warnings suppressed. It compiles with `PyCF_ONLY_AST`: no
    bytecode is produced, nothing is imported, and no `__pycache__` is written.
26. Each failure is an error `Diagnostic` plus `UnresolvedReference(syntax_error)` with
    `raw = <code>`. The codes are:
    - `encoding_error`, `null_bytes`, `syntax_error`;
    - `syntax_newer_than_interpreter`;
    - `source_too_complex` (`RecursionError`/`MemoryError`);
    - `unreadable_file`, `not_a_regular_file`.
27. **Newer syntax** is a heuristic over the failing line only. It is checked against a small
    version-keyed table:
    - PEP 695 type aliases and type parameters (3.12);
    - PEP 758 unparenthesized `except` (3.14);
    - PEP 750 t-strings (3.14).
28. Expressions are walked with an explicit stack, so RIFT itself cannot recurse deeply.
    Statement nesting is bounded by the parser's 100 indentation levels.

### Cache placement (§30)

29. **The SQLite cache is deferred to M5.** M2 is pure analysis. The concrete reason: a file's
    edges depend on the whole-project module index and bindings, so a per-file cache key must
    also cover that index. That key design belongs with its consumers (graph and impact), not
    before them. `AnalysisResult` already carries `content_sha256` and `analyzer_version`, so it
    is cacheable as is.

## Alternatives considered

- **New `UnresolvedReason` values** (`monkeypatch`, `namespace_write`): clearer, but changes the
  protocol vocabulary. Rejected for now; `raw` keeps the distinction visible.
- **Edges from the enclosing function for function-level imports:** more precise, but a later
  module-level use of the same name would be missed. Rejected as less conservative.
- **Name-match for every attribute access (not only calls):** §9.3 scopes `possible` to calls.
  Rejected; it would flood the graph.
- **`ast.unparse` for `raw`:** its formatting differs across Python versions. Rejected.

## Consequences

The decisions are pinned by:
- `tests/unit/analysis/test_extraction.py`: symbols, naming, imports, flags, confidence tiers,
  scoping;
- `tests/unit/analysis/test_dynamic_behavior.py`: every §9.4 behavior and the star-import rules;
- `tests/unit/analysis/test_input_handling.py`: §9.1;
- `tests/unit/core/test_analysis_models.py`: model invariants;
- `tests/contract/test_determinism.py`: PYTHONHASHSEED 0 vs 4242, and round-trip of real
  results;
- `tests/security/test_analysis_never_executes.py`: canaries, and a scan for execution
  primitives.

Known limitations:
- Lambdas and comprehensions share the enclosing scope (over-approximated locals).
- A call through a variable bound to a function (`cb = f; cb()`) is not resolved.
- `.pyi` stubs are ignored.
- Results depend on the interpreter's grammar (recorded in `python_version`).
