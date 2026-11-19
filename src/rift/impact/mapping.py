"""SPEC §11 step 1: map changed files and line ranges to graph nodes (ADR-0010 §4).

- A changed symbol is one whose own lines intersect a changed line range. A function's own
  lines are its whole span, decorators included. A class's own lines are its span minus the
  spans of its members, so editing a method changes the method, not the class.
- The file's own node (its module node, or its file node if it has no module name) is always
  changed. It starts the reverse traversal (its importers become dependents) only when
  module-level code changed (a changed line outside every top-level symbol), the file was added,
  copied, renamed or type-changed, it is binary, or a symbol was removed from it.
- Removed symbols are found on the base side: the base revision's version of a modified or
  renamed file is parsed in memory (``analyze_sources``; never executed). Its symbols whose
  nodes no longer exist are changed nodes. That covers a removed or renamed function, and every
  symbol of a module that moved to a new module name.
- A deleted ``.py`` file maps to every node of its base-side analysis: its module node and
  all its symbols, so a deleted public symbol is public API removed. Only if the base side
  cannot be parsed (missing blob, parse or decode failure, oversized) does it fall back to its
  ``file:`` node, with reason ``deleted_without_analysis``. A rename to a path that is not
  analyzed is handled as a deletion of the old path. Deleted paths that the configured
  exclusions match map to nothing, like their modifications.
- A whitespace-only edit is a change like any other.
- A changed ``.pyi`` stub is not analyzed (ADR-0007), so it adds reason ``stub_not_analyzed``.
- Other files that are not analyzed (non-Python, ignored, excluded) map to nothing.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rift.analysis.python.analyzer import LIMITATION_OVERSIZED, ProjectAnalysis
from rift.core.errors import ErrorCode
from rift.core.ids import NodeId, file_node_id, module_node_id
from rift.core.models.analysis import AnalysisResult, limitation_sort_key, sort_unique
from rift.core.models.change import ChangedFile, ChangeStatus, LineRange
from rift.core.models.diagnostic import DiagnosticSeverity
from rift.core.models.evidence import Limitation
from rift.core.models.source import Symbol, SymbolKind
from rift.discovery.files import matches_exclude
from rift.graph.builder import pytest_node_of
from rift.impact.errors import ImpactError
from rift.impact.index import NodeFacts, ProjectIndex

LIMITATION_DELETED = "deleted_without_analysis"
LIMITATION_STUB = "stub_not_analyzed"

_WHOLE_FILE = frozenset({ChangeStatus.ADDED, ChangeStatus.COPIED})
_PATH_CHANGED = frozenset(
    {ChangeStatus.ADDED, ChangeStatus.COPIED, ChangeStatus.RENAMED, ChangeStatus.TYPE_CHANGED}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleAnchor:
    """References that name ``name`` (a module, or ``module.qualname`` of a removed symbol) but
    no longer resolve are linked to ``anchor``, the changed node standing for it (§5)."""

    name: str
    anchor: NodeId


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeMapping:
    """``changed_nodes`` (sorted) and the subset that ``starts`` the traversal; base-side
    ``removed`` node facts (they join the working graph); anchors sorted by name; reasons the
    mapping is incomplete (sorted)."""

    changed_nodes: tuple[NodeId, ...]
    starts: tuple[NodeId, ...]
    removed: tuple[NodeFacts, ...]
    anchors: tuple[ModuleAnchor, ...]
    limitations: tuple[Limitation, ...]


def _clip(ranges: Sequence[LineRange], start: int, end: int) -> list[tuple[int, int]]:
    return [
        (max(r.start, start), min(r.end, end)) for r in ranges if r.start <= end and start <= r.end
    ]


def _subtract(
    parts: list[tuple[int, int]], holes: Iterable[tuple[int, int]]
) -> list[tuple[int, int]]:
    for hole_start, hole_end in holes:
        remaining: list[tuple[int, int]] = []
        for start, end in parts:
            if hole_end < start or end < hole_start:
                remaining.append((start, end))
                continue
            if start < hole_start:
                remaining.append((start, hole_start - 1))
            if hole_end < end:
                remaining.append((hole_end + 1, end))
        parts = remaining
    return parts


def _parsed(base: AnalysisResult) -> bool:
    """The base side was parsed: no error diagnostic and not skipped as oversized."""
    return not any(d.severity is DiagnosticSeverity.ERROR for d in base.diagnostics) and not any(
        lim.kind == LIMITATION_OVERSIZED for lim in base.limitations
    )


def own_node(result: AnalysisResult) -> NodeId:
    """The node that stands for a file: its module node, or its file node if it has none."""
    if result.module is not None:
        return module_node_id(result.module)
    return file_node_id(result.file_path)


def _span(symbol: Symbol) -> tuple[int, int]:
    return (symbol.location.start_line, symbol.location.end_line)


def _node(symbol: Symbol) -> NodeId:
    return pytest_node_of(symbol) or NodeId(symbol.id)


@dataclass(frozen=True, slots=True)
class _FileChange:
    symbols: list[NodeId]
    module_level: bool


def _changed_symbols(result: AnalysisResult, ranges: Sequence[LineRange] | None) -> _FileChange:
    """Symbols whose own lines intersect ``ranges`` (every symbol if ``None``), and whether a
    changed line is module-level code."""
    symbols = [s for s in result.symbols if s.kind is not SymbolKind.MODULE]
    if ranges is None:
        return _FileChange(symbols=[_node(s) for s in symbols], module_level=True)
    members: dict[str, list[tuple[int, int]]] = {}
    for symbol in symbols:
        parent, _, _ = symbol.qualname.rpartition(".")
        if parent:
            members.setdefault(parent, []).append(_span(symbol))
    kinds = {s.qualname: s.kind for s in symbols}
    changed: list[NodeId] = []
    for symbol in symbols:
        parts = _clip(ranges, *_span(symbol))
        if parts and kinds[symbol.qualname] is SymbolKind.CLASS:
            parts = _subtract(parts, members.get(symbol.qualname, ()))
        if parts:
            changed.append(_node(symbol))
    top_level = [_span(s) for s in symbols if "." not in s.qualname]
    outside = _subtract([(r.start, r.end) for r in ranges], top_level)
    return _FileChange(symbols=changed, module_level=bool(outside))


class _Builder:
    def __init__(
        self, index: ProjectIndex, base: ProjectAnalysis | None, exclude: Sequence[str]
    ) -> None:
        self.index = index
        self.base = {r.file_path: r for r in base.results} if base is not None else {}
        self.exclude = exclude
        self.changed: set[NodeId] = set()
        self.starts: set[NodeId] = set()
        self.removed: dict[NodeId, NodeFacts] = {}
        self.anchors: dict[str, NodeId] = {}
        self.limitations: list[Limitation] = []

    def deleted(self, path: str) -> None:
        if not path.endswith(".py") or matches_exclude(path, self.exclude):
            return
        base = self.base.get(path)
        if base is not None and _parsed(base):
            for fact in self.index.add_removed_module(base):
                self._remove(fact)
            return
        fact = self.index.add_removed_file(path)
        self.changed.add(fact.node)
        self.starts.add(fact.node)
        self.removed[fact.node] = fact
        self.limitations.append(
            Limitation(
                kind=LIMITATION_DELETED,
                message="the deleted file's base side could not be parsed: only its file node "
                "is known",
                subject=path,
            )
        )
        if fact.module is not None:
            self.anchors.setdefault(fact.module, fact.node)

    def present(self, changed_file: ChangedFile, result: AnalysisResult) -> None:
        own = own_node(result)
        self.changed.add(own)
        whole = changed_file.status in _WHOLE_FILE or changed_file.is_binary
        change = _changed_symbols(result, None if whole else changed_file.changed_lines)
        self.changed.update(change.symbols)
        self.starts.update(change.symbols)
        removed = self._removed_symbols(changed_file, result)
        if change.module_level or changed_file.status in _PATH_CHANGED or whole or removed:
            self.starts.add(own)
        if result.module is not None:
            self.anchors.setdefault(result.module, own)
        if changed_file.status is ChangeStatus.RENAMED and changed_file.old_path is not None:
            old_module = self.index.module_name_for(changed_file.old_path)
            if old_module is not None and old_module != result.module:
                self.anchors.setdefault(old_module, own)
                gone = self.index.add_removed_module_path(old_module, changed_file.old_path)
                if gone is not None:  # the old importable path is removed API
                    self.changed.add(gone.node)
                    self.removed[gone.node] = gone

    def _removed_symbols(self, changed_file: ChangedFile, result: AnalysisResult) -> bool:
        base_path = changed_file.old_path or changed_file.path
        base = self.base.get(base_path)
        if base is None:
            return False
        current = {_node(s) for s in result.symbols}
        added = self.index.add_removed_symbols(base, keep=current)
        for fact in added:
            self._remove(fact)
        return bool(added)

    def _remove(self, fact: NodeFacts) -> None:
        """A base-side node that no longer exists: changed, a start, and an anchor."""
        self.changed.add(fact.node)
        self.starts.add(fact.node)
        self.removed[fact.node] = fact
        if fact.module is None:
            return
        name = fact.module if fact.symbol is None else f"{fact.module}.{fact.symbol.qualname}"
        self.anchors.setdefault(name, fact.node)

    def build(self) -> ChangeMapping:
        return ChangeMapping(
            changed_nodes=tuple(sorted(self.changed)),
            starts=tuple(sorted(self.starts)),
            removed=tuple(self.removed[n] for n in sorted(self.removed)),
            anchors=tuple(
                ModuleAnchor(name=n, anchor=self.anchors[n]) for n in sorted(self.anchors)
            ),
            limitations=sort_unique(self.limitations, limitation_sort_key),
        )


def map_changes(
    files: Iterable[ChangedFile],
    index: ProjectIndex,
    *,
    base: ProjectAnalysis | None = None,
    exclude: Sequence[str] = (),
) -> ChangeMapping:
    """Map a change set's files onto nodes. ``base`` is the base revision's analysis of the
    modified, renamed and deleted files' old versions (``None``: removed symbols are not looked
    for, and deleted files fall back to their file node)."""
    builder = _Builder(index, base, exclude)
    for changed_file in files:
        paths = [changed_file.path, changed_file.old_path or changed_file.path]
        if any(p.endswith(".pyi") for p in paths) and not matches_exclude(
            changed_file.path, exclude
        ):
            builder.limitations.append(
                Limitation(
                    kind=LIMITATION_STUB,
                    message="type stubs are not analyzed (ADR-0007): their API change is unseen",
                    subject=changed_file.path,
                )
            )
        if changed_file.status is ChangeStatus.DELETED:
            builder.deleted(changed_file.path)
            continue
        result = index.results.get(changed_file.path)
        if result is not None:
            builder.present(changed_file, result)
        elif changed_file.status is ChangeStatus.RENAMED and changed_file.old_path is not None:
            builder.deleted(changed_file.old_path)  # moved out of the analyzed set
    return builder.build()


def map_nodes(nodes: Iterable[NodeId], index: ProjectIndex) -> ChangeMapping:
    """A hypothetical change of exactly ``nodes`` (``--symbol``): analyzed nodes only."""
    wanted = tuple(sorted(set(nodes)))
    for node in wanted:
        if not index.knows(node):
            raise ImpactError(
                f"{node!r} is not an analyzed node",
                code=ErrorCode.UNKNOWN_NODE,
                hint="resolve the name with resolve_symbol first",
            )
    return ChangeMapping(
        changed_nodes=wanted, starts=wanted, removed=(), anchors=(), limitations=()
    )


def map_paths(paths: Iterable[str], index: ProjectIndex) -> ChangeMapping:
    """A hypothetical change of every line of ``paths`` (``rift impact PATH...``)."""
    changed: set[NodeId] = set()
    for path in sorted(set(paths)):
        result = index.results.get(path)
        if result is None:
            raise ImpactError(
                f"{path!r} is not an analyzed Python file",
                hint="pass a tracked or untracked-not-ignored .py path relative to the repo root",
            )
        changed.add(own_node(result))
        changed.update(_changed_symbols(result, None).symbols)
    ordered = tuple(sorted(changed))
    return ChangeMapping(
        changed_nodes=ordered, starts=ordered, removed=(), anchors=(), limitations=()
    )
