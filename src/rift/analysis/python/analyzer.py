"""Analyze a set of Python files into one :class:`AnalysisResult` per file (SPEC §9).

Pipeline: read+parse every file → module index → walk every parsed module → resolve every
pending reference against the whole project → sorted, validated results. Nothing here imports,
executes or compiles analyzed code.
"""

import ast
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rift.analysis.python.modules import ModuleIndex
from rift.analysis.python.options import (
    ANALYZER_NAME,
    ANALYZER_VERSION,
    AnalysisError,
    AnalysisOptions,
    running_python_version,
)
from rift.analysis.python.reading import FailureCode, ReadFailure, SourceFile, read_source
from rift.analysis.python.resolver import FileResolver, Resolver
from rift.analysis.python.walker import FileFacts, walk_module
from rift.core.ids import NodeId, SymbolId, file_node_id, module_node_id
from rift.core.models.analysis import (
    AnalysisResult,
    diagnostic_sort_key,
    edge_sort_key,
    limitation_sort_key,
    sort_unique,
    symbol_sort_key,
    unresolved_sort_key,
)
from rift.core.models.diagnostic import Diagnostic, DiagnosticSeverity
from rift.core.models.evidence import Limitation
from rift.core.models.graph import Edge, UnresolvedReason, UnresolvedReference
from rift.core.models.source import SourceLocation, Symbol, SymbolKind, Visibility
from rift.core.paths import is_repo_path
from rift.discovery.files import discover_python_files
from rift.discovery.source_roots import detect_source_roots, validate_source_root

LIMITATION_OVERSIZED = "oversized_file"
LIMITATION_UNNAMED = "unnamed_module"
LIMITATION_SHADOWED = "module_shadowed"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectAnalysis:
    """``results`` sorted by ``file_path``; ``limitations`` are project-level (discovery)."""

    source_roots: tuple[str, ...]
    results: tuple[AnalysisResult, ...]
    limitations: tuple[Limitation, ...]


def analyze_project(root: Path, options: AnalysisOptions | None = None) -> ProjectAnalysis:
    """Discover every Python file under ``root`` and analyze them together."""
    opts = options or AnalysisOptions()
    discovered = discover_python_files(root, exclude=opts.exclude)
    return analyze_files(root, discovered.files, options=opts, limitations=discovered.limitations)


def analyze_files(
    root: Path,
    files: Sequence[str],
    *,
    options: AnalysisOptions | None = None,
    limitations: Sequence[Limitation] = (),
) -> ProjectAnalysis:
    """Analyze exactly ``files`` (repo-relative POSIX paths) under ``root``."""
    opts = options or AnalysisOptions()
    if not root.is_absolute() or not root.is_dir():
        raise AnalysisError("analysis root must be an existing absolute directory")
    paths = sorted(set(files))
    for path in paths:
        # On Windows a backslash is a separator: "..\\x" would escape the root.
        windows_escape = os.name == "nt" and ("\\" in path or ":" in path)
        if not is_repo_path(path) or not path.endswith(".py") or windows_escape:
            raise AnalysisError(f"not a repository-relative .py path: {path!r}")
    roots = (
        tuple(validate_source_root(r) for r in opts.source_roots)
        if opts.source_roots is not None
        else detect_source_roots(paths)
    )
    sources = [read_source(root, path, max_bytes=opts.max_file_bytes) for path in paths]
    index = ModuleIndex(paths, roots)
    facts: dict[str, FileFacts] = {}
    failures: dict[str, ReadFailure] = {}
    for source in sources:
        if source.tree is not None and source.text is not None:
            walked = _walk(source, source.tree, source.text, index)
            if isinstance(walked, ReadFailure):
                failures[source.path] = walked
            else:
                facts[source.path] = walked
    resolver = Resolver(index, facts.values())
    for path, file_facts in list(facts.items()):
        try:
            FileResolver(resolver, file_facts).run()
        except RecursionError:  # safety net: one pathological file never sinks the project
            failures[path] = ReadFailure(
                code=FailureCode.SOURCE_TOO_COMPLEX,
                message="name resolution is too deep to analyze",
                line=1,
                col=0,
            )
    python_version = running_python_version()
    results = tuple(
        _result(source, index, facts.get(source.path), failures.get(source.path), python_version)
        for source in sources
    )
    return ProjectAnalysis(
        source_roots=roots,
        results=results,
        limitations=sort_unique(list(limitations), limitation_sort_key),
    )


def _own(path: str, module: str | None) -> NodeId:
    return module_node_id(module) if module is not None else file_node_id(path)


def _walk(
    source: SourceFile, tree: ast.Module, text: str, index: ModuleIndex
) -> FileFacts | ReadFailure:
    module = index.module_of(source.path)
    try:
        return walk_module(
            tree,
            path=source.path,
            module=module,
            text=text,
            own=_own(source.path, module),
            index=index,
        )
    except RecursionError:  # statement nesting is parser-bounded; this is a safety net
        return ReadFailure(
            code=FailureCode.SOURCE_TOO_COMPLEX,
            message="nesting is too deep to analyze",
            line=1,
            col=0,
        )


def _module_symbol(module: str, path: str, text: str | None) -> Symbol:
    lines = (text or "").splitlines()
    end_line, end_col = (len(lines), len(lines[-1].encode("utf-8"))) if lines else (1, 0)
    return Symbol(
        id=SymbolId(module_node_id(module)),
        kind=SymbolKind.MODULE,
        name=module.rsplit(".", 1)[-1],
        qualname=module,
        module=module,
        location=SourceLocation(
            path=path,
            start_line=1,
            start_col=0,
            end_line=end_line,
            end_col=end_col,
        ),
        visibility=_visibility(module.rsplit(".", 1)[-1], None),
        decorators=(),
    )


def _visibility(name: str, all_names: set[str] | None) -> Visibility:
    """SPEC §6.2: dunder; else ``__all__`` wins when present; else the leading underscore."""
    if name.startswith("__") and name.endswith("__") and len(name) > 4:  # noqa: PLR2004
        return Visibility.DUNDER
    if all_names is not None:
        return Visibility.PUBLIC if name in all_names else Visibility.PRIVATE
    return Visibility.PRIVATE if name.startswith("_") else Visibility.PUBLIC


def _symbols(facts: FileFacts) -> list[Symbol]:
    if facts.module is None:
        return []
    module = facts.module
    all_names = facts.all_names if facts.all_present and facts.all_literal else None
    symbols = [_module_symbol(module, facts.path, facts.text)]
    for qualname, draft in facts.symbols.items():
        symbols.append(
            Symbol(
                id=SymbolId(f"py:{module}:{qualname}"),
                kind=draft.kind,
                name=draft.name,
                qualname=qualname,
                module=module,
                location=SourceLocation(
                    path=facts.path,
                    start_line=draft.start_line,
                    start_col=draft.start_col,
                    end_line=draft.end_line,
                    end_col=draft.end_col,
                ),
                visibility=_visibility(draft.name, all_names if draft.top_level else None),
                decorators=tuple(draft.decorators),
            )
        )
    return symbols


def _failure_parts(
    path: str, own: NodeId, failure: ReadFailure
) -> tuple[Diagnostic, UnresolvedReference]:
    location = SourceLocation(
        path=path,
        start_line=failure.line,
        start_col=failure.col,
        end_line=failure.line,
        end_col=failure.col,
    )
    diagnostic = Diagnostic(
        severity=DiagnosticSeverity.ERROR,
        code=failure.code.value,
        message=failure.message,
        subject=path,
        location=location,
    )
    unresolved = UnresolvedReference(
        source=own, raw=failure.code.value, reason=UnresolvedReason.SYNTAX_ERROR, location=location
    )
    return diagnostic, unresolved


def _result(
    source: SourceFile,
    index: ModuleIndex,
    facts: FileFacts | None,
    walk_failure: ReadFailure | None,
    python_version: str,
) -> AnalysisResult:
    path = source.path
    module = index.module_of(path)
    own = _own(path, module)
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    unresolved: list[UnresolvedReference] = []
    diagnostics: list[Diagnostic] = []
    limitations: list[Limitation] = []
    failure = source.failure or walk_failure
    if facts is not None and walk_failure is None:
        symbols = _symbols(facts)
        edges = facts.edges
        unresolved = facts.unresolved
        diagnostics = facts.diagnostics
        limitations = facts.limitations
    elif module is not None:
        symbols = [_module_symbol(module, path, None)]
    if failure is not None:
        diagnostic, ref = _failure_parts(path, own, failure)
        diagnostics = [*diagnostics, diagnostic]
        unresolved = [*unresolved, ref]
    if source.oversized:
        limitations = [
            *limitations,
            Limitation(
                kind=LIMITATION_OVERSIZED,
                message=f"skipped: {source.size} bytes exceeds max_file_bytes",
                subject=path,
            ),
        ]
    if module is None:
        shadow = next((s for s in index.shadowed if s.path == path), None)
        limitations = [
            *limitations,
            Limitation(
                kind=LIMITATION_SHADOWED if shadow else LIMITATION_UNNAMED,
                message=(
                    f"module name {shadow.name!r} belongs to {shadow.winner!r}"
                    if shadow
                    else "no importable module name (a path segment is not an identifier)"
                ),
                subject=path,
            ),
        ]
    return AnalysisResult(
        file_path=path,
        module=module,
        content_sha256=source.sha256,
        analyzer_name=ANALYZER_NAME,
        analyzer_version=ANALYZER_VERSION,
        python_version=python_version,
        symbols=sort_unique(symbols, symbol_sort_key),
        edges=sort_unique(edges, edge_sort_key),
        unresolved=sort_unique(unresolved, unresolved_sort_key),
        diagnostics=sort_unique(diagnostics, diagnostic_sort_key),
        limitations=sort_unique(limitations, limitation_sort_key),
    )
