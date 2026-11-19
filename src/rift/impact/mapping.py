"""SPEC §11 step 1: map changed files and line ranges to graph nodes (ADR-0010 §4).

- A changed symbol is one whose span (decorators included) intersects a changed line range.
- The file's own node (its module node, or its file node if it has no module name) is always
  changed.
- An added or copied file has every line changed, so every symbol is changed.
- A renamed file maps under its new path. Its old module name is remembered, so references
  that still name the old module reach the change.
- A deleted ``.py`` file maps to its ``file:`` node only. There is no prior analysis until the
  M5 cache, so the result is ``partial`` (reason ``deleted_without_analysis``).
- A whitespace-only edit is a change like any other: git reports the lines, and the enclosing
  symbol is changed. RIFT does not guess which edits are behaviour-neutral.
- Files that are not analyzed (non-Python, ignored, excluded) map to nothing.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rift.core.ids import NodeId, file_node_id
from rift.core.models.analysis import limitation_sort_key, sort_unique
from rift.core.models.change import ChangedFile, ChangeStatus, LineRange
from rift.core.models.evidence import Limitation
from rift.core.models.source import SymbolKind
from rift.graph.builder import pytest_node_of
from rift.impact.errors import ImpactError
from rift.impact.index import ProjectIndex, module_name_for


@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleAnchor:
    """References that name ``module`` (or a member of it) but no longer resolve are linked to
    ``anchor``: the changed node standing for that module (ADR-0010 §5)."""

    module: str
    anchor: NodeId


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeMapping:
    """The changed nodes (sorted), the module anchors (sorted by module), and the reasons the
    mapping is incomplete (sorted)."""

    changed_nodes: tuple[NodeId, ...]
    anchors: tuple[ModuleAnchor, ...]
    limitations: tuple[Limitation, ...]


def _intersects(start: int, end: int, ranges: Sequence[LineRange]) -> bool:
    return any(r.start <= end and start <= r.end for r in ranges)


def _symbol_nodes(
    index: ProjectIndex, path: str, ranges: Sequence[LineRange] | None
) -> list[NodeId]:
    """Nodes of ``path``'s symbols whose span intersects ``ranges`` (every symbol if ``None``)."""
    result = index.results[path]
    nodes: list[NodeId] = []
    for symbol in result.symbols:
        if symbol.kind is SymbolKind.MODULE:
            continue
        span = symbol.location
        if ranges is None or _intersects(span.start_line, span.end_line, ranges):
            nodes.append(pytest_node_of(symbol) or NodeId(symbol.id))
    return nodes


def map_changes(files: Iterable[ChangedFile], index: ProjectIndex) -> ChangeMapping:
    """Map a change set's files onto nodes of the current analysis."""
    changed: set[NodeId] = set()
    anchors: dict[str, NodeId] = {}
    limitations: list[Limitation] = []
    roots = index.source_roots
    for changed_file in files:
        path = changed_file.path
        if changed_file.status is ChangeStatus.DELETED:
            if not path.endswith(".py"):
                continue
            node = file_node_id(path)
            changed.add(node)
            limitations.append(
                Limitation(
                    kind="deleted_without_analysis",
                    message="deleted file has no prior analysis (no cache before M5): "
                    "only its file node is known",
                    subject=path,
                )
            )
            old_module = module_name_for(path, roots)
            if old_module is not None:
                anchors.setdefault(old_module, node)
            continue
        result = index.results.get(path)
        if result is None:
            continue  # not analyzed: not Python, ignored or excluded
        own = index.own_node(result)
        changed.add(own)
        whole = changed_file.status in {ChangeStatus.ADDED, ChangeStatus.COPIED}
        changed.update(_symbol_nodes(index, path, None if whole else changed_file.changed_lines))
        if result.module is not None:
            anchors.setdefault(result.module, own)
        if changed_file.status is ChangeStatus.RENAMED and changed_file.old_path is not None:
            old_module = module_name_for(changed_file.old_path, roots)
            if old_module is not None and old_module != result.module:
                anchors.setdefault(old_module, own)
    return ChangeMapping(
        changed_nodes=tuple(sorted(changed)),
        anchors=tuple(ModuleAnchor(module=m, anchor=anchors[m]) for m in sorted(anchors)),
        limitations=sort_unique(limitations, limitation_sort_key),
    )


def map_nodes(nodes: Iterable[NodeId], index: ProjectIndex) -> ChangeMapping:
    """A hypothetical change of exactly ``nodes`` (``--symbol``): no anchors, no limitations."""
    wanted = sorted(set(nodes))
    for node in wanted:
        index.facts(node)  # raises for a node that is not analyzed
    return ChangeMapping(changed_nodes=tuple(wanted), anchors=(), limitations=())


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
        changed.add(index.own_node(result))
        changed.update(_symbol_nodes(index, path, None))
    return ChangeMapping(changed_nodes=tuple(sorted(changed)), anchors=(), limitations=())
