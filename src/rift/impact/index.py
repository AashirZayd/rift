"""Facts about every node that impact needs (ADR-0010 §3): its file, module and unit, its
symbol, whether it is test code, and whether it is public."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from rift.analysis.python.analyzer import ProjectAnalysis
from rift.analysis.python.modules import ModuleIndex
from rift.analysis.python.naming import is_test_path
from rift.core.errors import ErrorCode
from rift.core.ids import NodeId, file_node_id, module_node_id
from rift.core.models.analysis import AnalysisResult
from rift.core.models.source import Symbol, SymbolKind, Visibility
from rift.discovery.source_roots import REPO_ROOT
from rift.graph.builder import pytest_node_of
from rift.impact.errors import ImpactError

_TEST_DIRECTORIES = frozenset({"tests", "test"})


def is_test_code_path(path: str) -> bool:
    """Test code (ADR-0010 §7): a pytest test file (``test_*.py`` / ``*_test.py``), a
    ``conftest.py``, or any file under a ``tests/`` or ``test/`` directory (helpers, factories,
    fixtures)."""
    parts = path.split("/")
    return (
        is_test_path(path)
        or parts[-1] == "conftest.py"
        or bool(_TEST_DIRECTORIES & set(parts[:-1]))
    )


def _is_private(segment: str) -> bool:
    if segment.startswith("<"):
        return True
    dunder = segment.startswith("__") and segment.endswith("__") and len(segment) > 4  # noqa: PLR2004  # "__x__"
    return segment.startswith("_") and not dunder


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeFacts:
    """``unit`` is the top-level package of ``module``; a module-less file is its own unit
    (``file:<path>``). ``symbol`` is set for class, function, method, constant and test nodes.
    ``top_visibility`` is the analyzer's visibility of the symbol's top-level ancestor (it
    honours ``__all__``). ``removed`` marks a node that exists only on the base side (a removed
    symbol, or a deleted file's node); ``unanalyzed`` marks a deleted file whose base side could
    not be parsed, so its symbols are unknown."""

    node: NodeId
    path: str
    module: str | None
    unit: str
    symbol: Symbol | None
    is_test_code: bool
    under_source_roots: bool
    top_visibility: Visibility | None = None
    removed: bool = False
    unanalyzed: bool = False

    @property
    def is_symbol(self) -> bool:
        return self.symbol is not None

    @property
    def module_key(self) -> str:
        return self.module if self.module is not None else f"file:{self.path}"

    @property
    def public_path(self) -> bool:
        """No segment of the dotted module path starts with ``_`` (dunders included: a
        ``pkg/__main__.py`` path is not public API; ADR-0010 follow-up), and the file is not test
        code. Symbol qualname segments keep the dunder exception (``__init__`` is public)."""
        if self.module is None or self.is_test_code:
            return False
        return not any(segment.startswith("_") for segment in self.module.split("."))

    @property
    def is_public(self) -> bool:
        """A public symbol (ADR-0010 §8): a public module path, a top-level ancestor that the
        analyzer reports as public (``__all__`` decides when present; dunders are public), and
        no private or ``<locals>``-style nested segment."""
        if self.symbol is None or not self.public_path:
            return False
        if self.top_visibility is Visibility.PRIVATE:
            return False
        return not any(_is_private(s) for s in self.symbol.qualname.split(".")[1:])


def unit_of(module: str | None, path: str) -> str:
    return module.split(".", 1)[0] if module else f"file:{path}"


def _symbol_facts(
    result: AnalysisResult, *, under_roots: bool, removed: bool = False
) -> list[NodeFacts]:
    is_test = is_test_code_path(result.file_path)
    unit = unit_of(result.module, result.file_path)
    top = {s.qualname: s.visibility for s in result.symbols if "." not in s.qualname}
    facts: list[NodeFacts] = []
    for symbol in result.symbols:
        if symbol.kind is SymbolKind.MODULE:
            node, kept, visibility = module_node_id(symbol.module), None, None
        else:
            node = pytest_node_of(symbol) or NodeId(symbol.id)
            kept, visibility = symbol, top.get(symbol.qualname.split(".", 1)[0])
        facts.append(
            NodeFacts(
                node=node,
                path=result.file_path,
                module=result.module,
                unit=unit,
                symbol=kept,
                is_test_code=is_test,
                under_source_roots=under_roots,
                top_visibility=visibility,
                removed=removed,
            )
        )
    return facts


class ProjectIndex:
    """Node facts for every analyzed file, extendable with base-side (removed) nodes."""

    def __init__(self, analysis: ProjectAnalysis) -> None:
        self.source_roots = analysis.source_roots
        self._analyzed_paths = tuple(r.file_path for r in analysis.results)
        modules = ModuleIndex(self._analyzed_paths, self.source_roots)
        facts: dict[NodeId, NodeFacts] = {}
        results: dict[str, AnalysisResult] = {}
        for result in analysis.results:
            results[result.file_path] = result
            under_roots = not modules.is_fallback(result.file_path)
            own = file_node_id(result.file_path)
            facts[own] = NodeFacts(
                node=own,
                path=result.file_path,
                module=result.module,
                unit=unit_of(result.module, result.file_path),
                symbol=None,
                is_test_code=is_test_code_path(result.file_path),
                under_source_roots=under_roots,
            )
            for fact in _symbol_facts(result, under_roots=under_roots):
                facts[fact.node] = fact
        self._facts: dict[NodeId, NodeFacts] = facts
        self._modules = modules
        self.results: Mapping[str, AnalysisResult] = MappingProxyType(results)

    def knows(self, node: NodeId) -> bool:
        return node in self._facts

    def facts(self, node: NodeId) -> NodeFacts:
        try:
            return self._facts[node]
        except KeyError:
            raise ImpactError(f"no facts for node {node!r}", code=ErrorCode.UNKNOWN_NODE) from None

    def module_name_for(self, path: str) -> str | None:
        """The module name the analyzer gives ``path`` among the analyzed files (fallback naming
        and shadowing included): the name a deleted or moved file had."""
        if path in self.results:
            return self.results[path].module
        return ModuleIndex([*self._analyzed_paths, path], self.source_roots).module_of(path)

    def top_level_names(self) -> frozenset[str]:
        """The top-level package or module names of the project (every source root, fallback-named
        files included), on the head side and the base side: a deleted top-level module is still
        the project's own name."""
        return frozenset(f.module.split(".", 1)[0] for f in self._facts.values() if f.module)

    def localized(self, path: str, name: str) -> str | None:
        """pytest's ``prepend`` mode lets a fallback-named file import a sibling by a short name
        (``import helpers`` in ``tests/`` means ``tests.helpers``, ADR-0008): the long name, or
        ``None`` for a file under the source roots."""
        if path in self.results and not self._modules.is_fallback(path):
            return None
        prefix = self._modules.pytest_base_package(path)
        return f"{prefix}.{name}" if prefix else None

    def add_removed_module(self, base: AnalysisResult) -> list[NodeFacts]:
        """Facts for every node of a deleted file, from its base-side analysis: its own node
        (module node, or file node if it had no module name) and all its symbols."""
        under_roots = self._under_roots(base.file_path)
        facts = _symbol_facts(base, under_roots=under_roots, removed=True)
        if base.module is None:
            facts.append(
                NodeFacts(
                    node=file_node_id(base.file_path),
                    path=base.file_path,
                    module=None,
                    unit=unit_of(None, base.file_path),
                    symbol=None,
                    is_test_code=is_test_code_path(base.file_path),
                    under_source_roots=under_roots,
                    removed=True,
                )
            )
        added: list[NodeFacts] = []
        for fact in facts:
            if fact.node not in self._facts:
                self._facts[fact.node] = fact
                added.append(fact)
        return added

    def add_removed_file(self, path: str) -> NodeFacts:
        """Facts for the ``file:`` node of a deleted (or moved-away) file whose base side could
        not be parsed."""
        module = self.module_name_for(path)
        node = file_node_id(path)
        fact = NodeFacts(
            node=node,
            path=path,
            module=module,
            unit=unit_of(module, path),
            symbol=None,
            is_test_code=is_test_code_path(path),
            under_source_roots=self._under_roots(path),
            removed=True,
            unanalyzed=True,
        )
        self._facts.setdefault(node, fact)
        return self._facts[node]

    def add_removed_symbols(self, base: AnalysisResult, keep: Iterable[NodeId]) -> list[NodeFacts]:
        """Facts for base-side symbols of ``base`` whose nodes no longer exist, except ``keep``."""
        kept = frozenset(keep)
        added: list[NodeFacts] = []
        for fact in _symbol_facts(
            base, under_roots=self._under_roots(base.file_path), removed=True
        ):
            if fact.symbol is None or fact.node in self._facts or fact.node in kept:
                continue
            self._facts[fact.node] = fact
            added.append(fact)
        return added

    def add_removed_module_path(self, module: str, path: str) -> NodeFacts | None:
        """The module node of a module name that no longer exists (a module renamed away): its
        importable path is gone. ``None`` if that node still exists."""
        node = module_node_id(module)
        if node in self._facts:
            return None
        fact = NodeFacts(
            node=node,
            path=path,
            module=module,
            unit=unit_of(module, path),
            symbol=None,
            is_test_code=is_test_code_path(path),
            under_source_roots=self._under_roots(path),
            removed=True,
        )
        self._facts[node] = fact
        return fact

    def register(self, fact: NodeFacts) -> None:
        """Add a removed node's facts (made by another index for the same analysis)."""
        self._facts.setdefault(fact.node, fact)

    def _under_roots(self, path: str) -> bool:
        if REPO_ROOT in self.source_roots:
            return True
        return any(path.startswith(root + "/") for root in self.source_roots)

    def public_units(self) -> tuple[str, ...]:
        """Default ``project.public_packages``: every top-level package of non-test code under
        the source roots (SPEC §11.1), base-side (deleted) packages included."""
        return tuple(
            sorted(
                {
                    f.unit
                    for f in self._facts.values()
                    if f.module and f.under_source_roots and not f.is_test_code
                }
            )
        )
