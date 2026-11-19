"""Per-file static analysis output (SPEC §9.5).

Every collection is stored sorted by a documented key and without duplicates (SPEC §7.2), so two
analyses of the same bytes are equal value-for-value. The keys are public so producers sort with
exactly the key the model checks.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from rift.core.ids import NodeId, file_node_id, module_node_id
from rift.core.models._checks import check_types, is_hex, non_empty, require, strictly_sorted
from rift.core.models.diagnostic import Diagnostic
from rift.core.models.evidence import Limitation
from rift.core.models.graph import Edge, UnresolvedReference
from rift.core.models.source import SourceLocation, Symbol, SymbolKind
from rift.core.paths import validate_repo_path

T = TypeVar("T")
_PYTHON_VERSION = re.compile(r"^[0-9]+\.[0-9]+$")  # ASCII digits only (ADR-0005 §10)

LocationKey = tuple[str, int, int, int, int]


def location_key(location: SourceLocation | None) -> LocationKey:
    """``(path, start_line, start_col, end_line, end_col)``; ``None`` sorts first."""
    if location is None:
        return ("", 0, 0, 0, 0)
    return (
        location.path,
        location.start_line,
        location.start_col,
        location.end_line,
        location.end_col,
    )


def symbol_sort_key(symbol: Symbol) -> tuple[str]:
    """Symbols: by id."""
    return (str(symbol.id),)


def edge_sort_key(edge: Edge) -> tuple[str, str, str, str, str, tuple[str, ...]]:
    """Edges: ``(source, kind, target, origin, confidence, flags)`` (SPEC §7.2 extended)."""
    return (
        edge.source,
        edge.kind.value,
        edge.target,
        edge.origin.value,
        edge.confidence.value,
        tuple(sorted(flag.value for flag in edge.flags)),
    )


def unresolved_sort_key(ref: UnresolvedReference) -> tuple[LocationKey, str, str, str]:
    """Unresolved references: ``(location, reason, raw, source)``."""
    return (location_key(ref.location), ref.reason.value, ref.raw, ref.source)


def diagnostic_sort_key(diag: Diagnostic) -> tuple[LocationKey, str, str, str, str]:
    """Diagnostics: ``(location, code, severity, message, subject)``."""
    return (
        location_key(diag.location),
        diag.code,
        diag.severity.value,
        diag.message,
        diag.subject or "",
    )


def limitation_sort_key(lim: Limitation) -> tuple[str, str, str]:
    """Limitations: ``(kind, subject, message)``."""
    return (lim.kind, lim.subject or "", lim.message)


def sort_unique(values: Sequence[T], key: Callable[[T], Any]) -> tuple[T, ...]:
    """Sort by ``key`` and drop exact duplicates. Values with equal keys but different contents
    are kept, and the model then rejects them, so a producer bug is never silently hidden."""
    result: list[T] = []
    for value in sorted(values, key=key):
        if not result or result[-1] != value:
            result.append(value)
    return tuple(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisResult:
    """Static facts about one file. ``module`` is ``None`` when the file has no importable
    module name; it then has no symbols and its edges originate from its ``file:`` node.
    ``python_version`` is the analyzer interpreter's ``major.minor`` (SPEC §9.1): parse results
    depend on it."""

    file_path: str
    module: str | None
    content_sha256: str
    analyzer_name: str
    analyzer_version: str
    python_version: str
    symbols: tuple[Symbol, ...]
    edges: tuple[Edge, ...]
    unresolved: tuple[UnresolvedReference, ...]
    diagnostics: tuple[Diagnostic, ...]
    limitations: tuple[Limitation, ...]

    def __post_init__(self) -> None:
        check_types(self)
        validate_repo_path(self.file_path, "file_path")
        require(is_hex(self.content_sha256, (64,)), "content_sha256 must be 64 lowercase hex")
        non_empty(self.analyzer_name, "analyzer_name")
        non_empty(self.analyzer_version, "analyzer_version")
        require(
            bool(_PYTHON_VERSION.match(self.python_version)),
            "python_version must be major.minor",
        )
        strictly_sorted(self.symbols, symbol_sort_key, "symbols")
        strictly_sorted(self.edges, edge_sort_key, "edges")
        strictly_sorted(self.unresolved, unresolved_sort_key, "unresolved")
        strictly_sorted(self.diagnostics, diagnostic_sort_key, "diagnostics")
        strictly_sorted(self.limitations, limitation_sort_key, "limitations")
        own: set[NodeId] = {file_node_id(self.file_path)}
        if self.module is None:
            require(not self.symbols, "a file without a module name has no symbols")
        else:
            own.add(module_node_id(self.module))
        for symbol in self.symbols:
            require(symbol.module == self.module, f"symbol {symbol.id} is not in {self.module}")
            require(symbol.location.path == self.file_path, f"symbol {symbol.id} is elsewhere")
            require(
                symbol.kind is not SymbolKind.MODULE or NodeId(symbol.id) in own, "foreign module"
            )
            own.add(NodeId(symbol.id))
        for edge in self.edges:
            require(edge.source in own, f"edge source {edge.source} is not defined in this file")
        for ref in self.unresolved:
            require(ref.source in own, f"unresolved source {ref.source} is not in this file")
            require(ref.location.path == self.file_path, "unresolved location is elsewhere")
