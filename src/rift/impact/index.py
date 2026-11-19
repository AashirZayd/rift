"""Facts about every node that impact needs: its file, module, top-level unit, whether it is a
symbol, and whether it is test code (ADR-0010 §3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from rift.analysis.python.analyzer import ProjectAnalysis
from rift.analysis.python.naming import is_test_path
from rift.core.errors import ErrorCode
from rift.core.ids import NodeId, file_node_id, module_node_id
from rift.core.models.analysis import AnalysisResult
from rift.core.models.source import Symbol, SymbolKind
from rift.discovery.source_roots import importable_name
from rift.graph.builder import pytest_node_of
from rift.impact.errors import ImpactError


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeFacts:
    """``unit`` is the top-level package of ``module``; a file without a module name is its own
    unit (``file:<path>``). ``qualname`` and ``symbol`` are set for class, function, method,
    constant and test nodes only."""

    node: NodeId
    path: str
    module: str | None
    unit: str
    symbol: Symbol | None
    is_test_code: bool

    @property
    def is_symbol(self) -> bool:
        return self.symbol is not None

    @property
    def is_public(self) -> bool:
        """No private segment in the module path or the qualname (``_x``; dunders are public),
        no ``<locals>``-style segment, and not test code."""
        if self.symbol is None or self.module is None or self.is_test_code:
            return False
        segments = [*self.module.split("."), *self.symbol.qualname.split(".")]
        return not any(_is_private(segment) for segment in segments)


def _is_private(segment: str) -> bool:
    if segment.startswith("<"):
        return True
    dunder = segment.startswith("__") and segment.endswith("__") and len(segment) > 4  # noqa: PLR2004  # "__x__"
    return segment.startswith("_") and not dunder


def module_name_for(path: str, source_roots: Sequence[str]) -> str | None:
    """The module name ``path`` has (or had) under ``source_roots``; ``None`` if not importable.
    The most specific root containing the path wins."""
    for root in sorted(source_roots, key=lambda r: (-len(r), r)):
        if root == ".":
            return importable_name(path)
        if path.startswith(root + "/"):
            return importable_name(path[len(root) + 1 :])
    return None


def unit_of(module: str | None, path: str) -> str:
    return module.split(".", 1)[0] if module else f"file:{path}"


class ProjectIndex:
    """Node facts for every analyzed file, plus facts derived from a path for files that are no
    longer analyzed (a deleted file's ``file:`` node)."""

    def __init__(self, analysis: ProjectAnalysis) -> None:
        self.source_roots = analysis.source_roots
        facts: dict[NodeId, NodeFacts] = {}
        results: dict[str, AnalysisResult] = {}
        for result in analysis.results:
            results[result.file_path] = result
            is_test = is_test_path(result.file_path)
            unit = unit_of(result.module, result.file_path)
            own = file_node_id(result.file_path)
            facts[own] = NodeFacts(
                node=own,
                path=result.file_path,
                module=result.module,
                unit=unit,
                symbol=None,
                is_test_code=is_test,
            )
            for symbol in result.symbols:
                node = pytest_node_of(symbol) or NodeId(symbol.id)
                facts[node] = NodeFacts(
                    node=node,
                    path=result.file_path,
                    module=result.module,
                    unit=unit,
                    symbol=None if symbol.kind is SymbolKind.MODULE else symbol,
                    is_test_code=is_test,
                )
        self._facts: Mapping[NodeId, NodeFacts] = MappingProxyType(facts)
        self.results: Mapping[str, AnalysisResult] = MappingProxyType(results)

    def facts(self, node: NodeId) -> NodeFacts:
        """Facts of an analyzed node, or of a ``file:`` node of a file that is gone."""
        known = self._facts.get(node)
        if known is not None:
            return known
        prefix, _, path = node.partition(":")
        if prefix != "file":
            raise ImpactError(f"no facts for node {node!r}", code=ErrorCode.UNKNOWN_NODE)
        module = module_name_for(path, self.source_roots)
        return NodeFacts(
            node=node,
            path=path,
            module=module,
            unit=unit_of(module, path),
            symbol=None,
            is_test_code=is_test_path(path),
        )

    def own_node(self, result: AnalysisResult) -> NodeId:
        """The node that stands for a file: its module node, or its file node if it has none."""
        if result.module is not None:
            return module_node_id(result.module)
        return file_node_id(result.file_path)

    def public_units(self) -> tuple[str, ...]:
        """Default ``project.public_packages``: every top-level package of non-test code."""
        return tuple(
            sorted({f.unit for f in self._facts.values() if f.module and not f.is_test_code})
        )
