"""Single pass over one parsed module: symbols, bindings, imports and pending references.

Statements are visited recursively (nesting depth is bounded by the parser's 100 indentation
levels); expressions are walked with an explicit stack, so arbitrarily deep expressions never
recurse inside RIFT. Name resolution needs every module's bindings, so references are collected
here as :class:`Pending` items and resolved afterwards by :mod:`rift.analysis.python.resolver`.
"""

import ast
import re
from dataclasses import dataclass, field
from enum import StrEnum

from rift.analysis.python.modules import ModuleIndex
from rift.analysis.python.scopes import (
    Binding,
    BindingKind,
    Scope,
    ScopeKind,
    class_extra_names,
    collect_scope_names,
    function_extra_names,
)
from rift.core.ids import NodeId, is_qualname, symbol_node_id
from rift.core.models.diagnostic import Diagnostic, DiagnosticSeverity
from rift.core.models.evidence import Limitation
from rift.core.models.graph import (
    Confidence,
    Edge,
    EdgeFlag,
    EdgeKind,
    EdgeOrigin,
    UnresolvedReason,
    UnresolvedReference,
)
from rift.core.models.source import SourceLocation, SymbolKind

_CONSTANT = re.compile(r"^_*[A-Z][A-Z0-9_]*$")
_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})
_RAW_LIMIT = 160
_BRANCHING = (ast.For, ast.AsyncFor, ast.While, ast.Match)

LIMITATION_NON_LITERAL_ALL = "non_literal_all"
LIMITATION_UNREPRESENTABLE_SYMBOL = "unrepresentable_symbol"
DIAG_RELATIVE_BEYOND_TOP = "relative_import_beyond_top_level"
DIAG_RELATIVE_NO_PACKAGE = "relative_import_without_package"


class PendingKind(StrEnum):
    CALL = "call"  # node: ast.Call
    REFERENCE = "reference"  # node: Name/Attribute chain in Load context
    BASE = "base"  # node: a class base expression (chain)
    DECORATOR = "decorator"  # node: decorator chain (the callee if the decorator is a call)
    STORE = "store"  # node: Attribute/Subscript assignment or deletion target


@dataclass(slots=True, kw_only=True)
class Pending:
    kind: PendingKind
    node: ast.expr
    scope: Scope
    source: NodeId


@dataclass(slots=True, kw_only=True)
class NameCheck:
    """``from base import name``: checked once every module's bindings are known."""

    base: str
    name: str
    node: ast.stmt
    source: NodeId


@dataclass(slots=True, kw_only=True)
class StarImport:
    base: str
    internal: bool
    node: ast.stmt


@dataclass(slots=True, kw_only=True)
class ClassFacts:
    qualname: str
    members: frozenset[str]
    bases: tuple[ast.expr, ...]
    scope: Scope  # where the bases are evaluated


@dataclass(slots=True, kw_only=True)
class SymbolDraft:
    kind: SymbolKind
    name: str
    qualname: str
    top_level: bool
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    decorators: list[str]

    def merge(self, other: "SymbolDraft") -> None:
        """A redefinition (property setter, try/except fallback): span both (ADR-0007)."""
        start = min((self.start_line, self.start_col), (other.start_line, other.start_col))
        end = max((self.end_line, self.end_col), (other.end_line, other.end_col))
        (self.start_line, self.start_col), (self.end_line, self.end_col) = start, end
        self.decorators += [d for d in other.decorators if d not in self.decorators]


@dataclass(slots=True, kw_only=True)
class FileFacts:
    path: str
    module: str | None
    text: str
    own: NodeId  # the module node, or the file node for an unnamed file
    module_scope: Scope
    is_test_file: bool
    symbols: dict[str, SymbolDraft] = field(default_factory=dict)
    classes: dict[str, ClassFacts] = field(default_factory=dict)
    all_names: set[str] = field(default_factory=set)
    all_present: bool = False
    all_literal: bool = True
    has_getattr: bool = False
    star_imports: list[StarImport] = field(default_factory=list)
    pending: list[Pending] = field(default_factory=list)
    name_checks: list[NameCheck] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[UnresolvedReference] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)
    constants: set[str] = field(default_factory=set)

    def is_ambiguous(self, name: str, bindings: int) -> bool:
        """Several candidate bindings, or a definition that a plain assignment may replace."""
        opaque = self.module_scope.opaque
        return bindings > 1 or (name in opaque and name not in self.constants)

    def node_for(self, qualname: str) -> NodeId | None:
        if self.module is None or not is_qualname(qualname):
            return None
        return symbol_node_id(self.module, qualname)

    def location(self, node: ast.AST) -> SourceLocation:
        line = max(getattr(node, "lineno", 1) or 1, 1)
        col = max(getattr(node, "col_offset", 0) or 0, 0)
        end_line = max(getattr(node, "end_lineno", None) or line, line)
        end_col = max(getattr(node, "end_col_offset", None) or col, 0)
        if end_line == line and end_col < col:
            end_col = col
        return SourceLocation(
            path=self.path, start_line=line, start_col=col, end_line=end_line, end_col=end_col
        )

    def raw(self, node: ast.AST) -> str:
        """Source text of ``node`` (whitespace collapsed, capped): version-independent."""
        segment = ast.get_source_segment(self.text, node) or type(node).__name__
        text = " ".join(segment.split())
        return text if len(text) <= _RAW_LIMIT else text[: _RAW_LIMIT - 3] + "..."

    def unresolved_at(
        self, node: ast.AST, source: NodeId, reason: UnresolvedReason, raw: str
    ) -> None:
        self.unresolved.append(
            UnresolvedReference(
                source=source, raw=raw or "?", reason=reason, location=self.location(node)
            )
        )

    def import_edge(
        self,
        target: str,
        flags: frozenset[EdgeFlag],
        *,
        confidence: Confidence = Confidence.CERTAIN,
    ) -> None:
        self.edges.append(
            Edge(
                source=self.own,
                target=NodeId(f"py:{target}"),
                kind=EdgeKind.IMPORTS,
                origin=EdgeOrigin.AST_STATIC,
                confidence=confidence,
                evidence_ids=(),
                flags=flags,
            )
        )


def chain_of(expr: ast.expr) -> tuple[ast.expr, list[str]]:
    """``a.b.c`` → (Name a, ["b", "c"]); the root is the innermost non-attribute expression."""
    attrs: list[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    attrs.reverse()
    return node, attrs


def dotted(expr: ast.expr) -> str | None:
    root, attrs = chain_of(expr)
    if isinstance(root, ast.Name):
        return ".".join([root.id, *attrs])
    return None


def _is_test_file(path: str) -> bool:
    """pytest's default ``python_files = test_*.py *_test.py`` (static classification only)."""
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def _literal_strings(node: ast.expr | None) -> list[str] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    values: list[str] = []
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            return None
        values.append(element.value)
    return values


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        name = dotted(t)
        if name is not None and name.rsplit(".", 1)[-1] in _IMPORT_ERRORS:
            return True
    return False


def store_name(scope: Scope, name: str) -> None:
    """A plain store (assignment, loop/with/except/match target, ``del``) of ``name``."""
    target = scope.module() if name in scope.declared_global else scope
    target.opaque.add(name)


def _is_type_checking(test: ast.expr, scope: Scope) -> bool:
    """``TYPE_CHECKING``, ``<x>.TYPE_CHECKING``, or a name imported as ``TYPE_CHECKING``."""
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    if not isinstance(test, ast.Name):
        return False
    if test.id == "TYPE_CHECKING":
        return True
    current: Scope | None = scope
    while current is not None:
        for binding in current.bindings.get(test.id, ()):
            if binding.kind is BindingKind.IMPORTED and binding.attr == "TYPE_CHECKING":
                return True
        current = current.parent
    return False


def _is_staticmethod(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(dotted(d) in {"staticmethod", "builtins.staticmethod"} for d in node.decorator_list)


# Implicit classmethods: the interpreter passes the class as the first argument.
_IMPLICIT_CLASSMETHODS = frozenset({"__init_subclass__", "__class_getitem__"})


def _is_classmethod(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return node.name in _IMPLICIT_CLASSMETHODS or any(
        dotted(d) in {"classmethod", "builtins.classmethod"} for d in node.decorator_list
    )


class Walker:
    """Builds the :class:`FileFacts` of one module. Use :func:`walk_module`."""

    def __init__(self, facts: FileFacts, index: ModuleIndex) -> None:
        self.facts = facts
        self.index = index

    # ------------------------------------------------------------------ statements

    def body(self, stmts: list[ast.stmt], scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        for stmt in stmts:
            self.stmt(stmt, scope, flags)

    def stmt(self, node: ast.stmt, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            self.function(node, scope)
        elif isinstance(node, ast.ClassDef):
            self.class_def(node, scope)
        elif isinstance(node, ast.Import):
            self.import_stmt(node, scope, flags)
        elif isinstance(node, ast.ImportFrom):
            self.import_from(node, scope, flags)
        elif isinstance(node, ast.If):
            self.if_stmt(node, scope, flags)
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            self.try_stmt(node, scope, flags)
        else:
            if scope.kind is ScopeKind.MODULE:
                self.module_level(node)
            if isinstance(node, _BRANCHING):  # bodies run zero or more times
                flags = flags | {EdgeFlag.CONDITIONAL}
            self.generic(node, scope, flags)

    def generic(self, node: ast.AST, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        """Visit every child: statements recursively, expressions iteratively."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self.stmt(child, scope, flags)
            elif isinstance(child, ast.expr):
                self.expr(child, scope, scope.source)
            else:  # withitem, match_case, excepthandler, keyword, pattern, ...
                if isinstance(child, ast.MatchAs | ast.MatchStar) and child.name:
                    store_name(scope, child.name)
                elif isinstance(child, ast.MatchMapping) and child.rest:
                    store_name(scope, child.rest)
                self.generic(child, scope, flags)

    def if_stmt(self, node: ast.If, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        if _is_type_checking(node.test, scope):
            self.body(node.body, scope, flags | {EdgeFlag.TYPE_CHECKING_ONLY})
        else:
            self.expr(node.test, scope, scope.source)
            self.body(node.body, scope, flags | {EdgeFlag.CONDITIONAL})
        self.body(node.orelse, scope, flags | {EdgeFlag.CONDITIONAL})

    def try_stmt(self, node: ast.stmt, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        handlers: list[ast.ExceptHandler] = getattr(node, "handlers", [])
        guarded = any(_catches_import_error(h) for h in handlers)
        inner = flags | {EdgeFlag.CONDITIONAL} if guarded else flags
        self.body(getattr(node, "body", []), scope, inner)
        for handler in handlers:
            if handler.name:
                store_name(scope, handler.name)
            if handler.type is not None:
                self.expr(handler.type, scope, scope.source)
            self.body(handler.body, scope, inner)
        self.body(getattr(node, "orelse", []), scope, inner)
        self.body(getattr(node, "finalbody", []), scope, flags)

    # ------------------------------------------------------------------ definitions

    def _symbol(
        self,
        qualname: str,
        kind: SymbolKind,
        node: ast.AST,
        decorators: list[ast.expr],
        *,
        top_level: bool,
    ) -> NodeId | None:
        facts = self.facts
        if facts.module is None:
            return None
        node_id = facts.node_for(qualname)
        if node_id is None:
            facts.limitations.append(
                Limitation(
                    kind=LIMITATION_UNREPRESENTABLE_SYMBOL,
                    message=f"symbol name is not an ASCII identifier: {qualname!r}",
                    subject=facts.path,
                )
            )
            return None
        loc = facts.location(node)
        start_line, start_col = loc.start_line, loc.start_col
        for dec in decorators:  # the symbol's span includes its decorators
            if dec.lineno < start_line:
                start_line, start_col = dec.lineno, self._at_column(dec)
        draft = SymbolDraft(
            kind=kind,
            name=qualname.rsplit(".", 1)[-1],
            qualname=qualname,
            top_level=top_level,
            start_line=start_line,
            start_col=start_col,
            end_line=loc.end_line,
            end_col=loc.end_col if loc.end_line > start_line else max(loc.end_col, start_col),
            decorators=[self._decorator_text(d) for d in decorators],
        )
        existing = facts.symbols.get(qualname)
        if existing is None:
            facts.symbols[qualname] = draft
        else:
            existing.merge(draft)
        return node_id

    def _at_column(self, dec: ast.expr) -> int:
        """Byte column of the ``@`` introducing ``dec`` (``@ (lambda f: f)`` has spaces)."""
        lines = self.facts.text.splitlines()
        line = lines[dec.lineno - 1].encode("utf-8") if dec.lineno <= len(lines) else b""
        at = line.rfind(b"@", 0, dec.col_offset)
        return at if at >= 0 else max(dec.col_offset - 1, 0)

    def _decorator_text(self, dec: ast.expr) -> str:
        target = dec.func if isinstance(dec, ast.Call) else dec
        return dotted(target) or self.facts.raw(dec)

    def _decorators(self, decorators: list[ast.expr], scope: Scope, source: NodeId) -> None:
        for dec in decorators:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if dotted(target) is not None:
                self.facts.pending.append(
                    Pending(kind=PendingKind.DECORATOR, node=target, scope=scope, source=source)
                )
            else:
                self.expr(target, scope, source)
            if isinstance(dec, ast.Call):
                for arg in [*dec.args, *(k.value for k in dec.keywords)]:
                    self.expr(arg, scope, scope.source)

    def function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope) -> None:
        qualname = scope.qualprefix + node.name
        is_method = scope.kind is ScopeKind.CLASS
        if is_method:
            in_test_class = (scope.class_qualname or "").rsplit(".", 1)[-1].startswith("Test")
            is_test = self.facts.is_test_file and in_test_class and node.name.startswith("test")
            kind = SymbolKind.TEST_METHOD if is_test else SymbolKind.METHOD
        else:
            top = scope.kind is ScopeKind.MODULE
            is_test = self.facts.is_test_file and top and node.name.startswith("test")
            kind = SymbolKind.TEST_FUNCTION if is_test else SymbolKind.FUNCTION
        node_id = self._symbol(
            qualname, kind, node, node.decorator_list, top_level=scope.kind is ScopeKind.MODULE
        )
        source = node_id or scope.source
        scope.bind(node.name, Binding(BindingKind.DEF, qualname))
        self._decorators(node.decorator_list, scope, source)
        args = node.args
        for expr in [
            *args.defaults,
            *(d for d in args.kw_defaults if d is not None),
            *(
                a.annotation
                for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                if a.annotation
            ),
            *(a.annotation for a in (args.vararg, args.kwarg) if a is not None and a.annotation),
            *([node.returns] if node.returns is not None else []),
        ]:
            self.expr(expr, scope, source)
        if scope.kind is ScopeKind.MODULE and node.name == "__getattr__":
            self.facts.has_getattr = True
            self.facts.unresolved_at(
                node, self.facts.own, UnresolvedReason.MODULE_GETATTR, "__getattr__"
            )
        bound, declared_global, declared_nonlocal = collect_scope_names(
            node.body, function_extra_names(node)
        )
        positional = [*args.posonlyargs, *args.args]
        self_name = (
            positional[0].arg if is_method and positional and not _is_staticmethod(node) else None
        )
        inner = Scope(
            kind=ScopeKind.FUNCTION,
            qualprefix=qualname + ".<locals>.",
            parent=scope,
            source=source,
            bound=bound,
            declared_global=declared_global,
            declared_nonlocal=declared_nonlocal,
            class_qualname=scope.class_qualname if is_method else None,
            self_name=self_name,
            is_classmethod=self_name is not None and _is_classmethod(node),
        )
        self.body(node.body, inner, frozenset())

    def class_def(self, node: ast.ClassDef, scope: Scope) -> None:
        qualname = scope.qualprefix + node.name
        node_id = self._symbol(
            qualname,
            SymbolKind.CLASS,
            node,
            node.decorator_list,
            top_level=scope.kind is ScopeKind.MODULE,
        )
        source = node_id or scope.source
        scope.bind(node.name, Binding(BindingKind.DEF, qualname))
        self._decorators(node.decorator_list, scope, source)
        for base in node.bases:
            if dotted(base) is not None:
                self.facts.pending.append(
                    Pending(kind=PendingKind.BASE, node=base, scope=scope, source=source)
                )
            else:
                self.expr(base, scope, source)
        for keyword in node.keywords:
            self.expr(keyword.value, scope, source)
        bound, declared_global, declared_nonlocal = collect_scope_names(
            node.body, class_extra_names(node)
        )
        inner = Scope(
            kind=ScopeKind.CLASS,
            qualprefix=qualname + ".",
            parent=scope,
            source=source,
            bound=bound,
            declared_global=declared_global,
            declared_nonlocal=declared_nonlocal,
            class_qualname=qualname,
        )
        self.facts.classes[qualname] = ClassFacts(
            qualname=qualname, members=bound, bases=tuple(node.bases), scope=scope
        )
        self.body(node.body, inner, frozenset())

    # ------------------------------------------------------------------ module-level facts

    def module_level(self, node: ast.stmt) -> None:
        """``__all__`` and UPPER_CASE constants (SPEC §9.2)."""
        if isinstance(node, ast.Assign):
            self._assignment(node, node.targets, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._assignment(node, [node.target], node.value)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                names = _literal_strings(node.value)
                self._all_update(names if isinstance(node.op, ast.Add) else None)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            self._all_method(node.value)

    def _all_update(self, names: list[str] | None, *, replace: bool = False) -> None:
        facts = self.facts
        facts.all_present = True
        if names is None:
            facts.all_literal = False
        elif replace:
            facts.all_names = set(names)
        else:
            facts.all_names.update(names)

    def _all_method(self, call: ast.Call) -> None:
        """``__all__.extend([...])`` / ``__all__.append("x")``; any other method is non-literal."""
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "__all__"
        ):
            return
        names: list[str] | None = None
        if len(call.args) == 1 and not call.keywords:
            arg = call.args[0]
            if func.attr == "extend":
                names = _literal_strings(arg)
            elif func.attr == "append" and isinstance(arg, ast.Constant):
                names = [arg.value] if isinstance(arg.value, str) else None
        self._all_update(names)

    def _assignment(self, node: ast.stmt, targets: list[ast.expr], value: ast.expr) -> None:
        for target in targets:
            names = target.elts if isinstance(target, ast.Tuple | ast.List) else [target]
            for name_node in names:
                if not isinstance(name_node, ast.Name):
                    continue
                if name_node.id == "__all__":
                    simple = len(targets) == 1 and len(names) == 1
                    self._all_update(_literal_strings(value) if simple else None, replace=True)
                elif _CONSTANT.match(name_node.id) and self._symbol(
                    name_node.id, SymbolKind.CONSTANT, node, [], top_level=True
                ):
                    self.facts.constants.add(name_node.id)
                    self.facts.module_scope.bind(
                        name_node.id, Binding(BindingKind.DEF, name_node.id)
                    )

    # ------------------------------------------------------------------ imports

    def _localize(self, name: str) -> tuple[str, Confidence]:
        """pytest's default ``prepend`` import mode puts a test file's base directory (the first
        ancestor without ``__init__.py``) on ``sys.path``, so ``tests/test_a.py`` can
        ``import helpers`` for ``tests/helpers.py``. Only for files outside the configured
        source roots, and only ``likely``: it depends on how the tests are run (ADR-0008)."""
        if self.index.exists(name) or not self.index.is_fallback(self.facts.path):
            return name, Confidence.CERTAIN
        prefix = self.index.pytest_base_package(self.facts.path)
        if prefix and self.index.exists(f"{prefix}.{name}"):
            return f"{prefix}.{name}", Confidence.LIKELY
        return name, Confidence.CERTAIN

    def _resolve_absolute(
        self,
        name: str,
        node: ast.stmt,
        flags: frozenset[EdgeFlag],
        confidence: Confidence = Confidence.CERTAIN,
    ) -> bool:
        """Edges for ``name`` and its regular ancestor packages; unresolved if it is missing.
        Returns whether ``name`` exists in the project (module or namespace package)."""
        facts = self.facts
        for ancestor in self.index.regular_ancestors(name):
            if ancestor != facts.module:  # a package's own __init__ is not a dependency
                facts.import_edge(ancestor, flags, confidence=confidence)
        if self.index.is_module(name):
            if name != facts.module:
                facts.import_edge(name, flags, confidence=confidence)
            return True
        if self.index.is_namespace(name):
            return True
        reason = (
            UnresolvedReason.MISSING_MODULE
            if self.index.is_internal(name)
            else UnresolvedReason.EXTERNAL_MODULE
        )
        facts.unresolved_at(node, facts.own, reason, name)
        return False

    def import_stmt(self, node: ast.Import, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        for alias in node.names:
            name, confidence = self._localize(alias.name)
            self._resolve_absolute(name, node, flags, confidence)
            if alias.asname:
                scope.bind(alias.asname, Binding(BindingKind.MODULE, name, confidence=confidence))
            else:  # ``import a.b`` binds ``a``
                top = name[: len(name) - len(alias.name)] + alias.name.split(".")[0]
                scope.bind(
                    alias.name.split(".")[0],
                    Binding(BindingKind.MODULE, top, confidence=confidence),
                )

    def _relative_base(self, node: ast.ImportFrom) -> str | None:
        """Absolute module for a relative import, or ``None`` (recorded) if it escapes the
        top-level package or the importing file has no module name."""
        facts = self.facts
        level = node.level or 0
        dots = "." * level + (node.module or "")
        package = self.index.package_of(facts.module) if facts.module is not None else None
        parts = package.split(".") if package else []
        if package is None or level - 1 >= len(parts):
            facts.unresolved_at(node, facts.own, UnresolvedReason.MISSING_MODULE, dots)
            unnamed = package is None
            facts.diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.WARNING,
                    code=DIAG_RELATIVE_NO_PACKAGE if unnamed else DIAG_RELATIVE_BEYOND_TOP,
                    message=(
                        f"relative import {dots!r} in a file without a module name"
                        if unnamed
                        else f"relative import {dots!r} goes beyond the top-level package"
                    ),
                    subject=facts.path,
                    location=facts.location(node),
                )
            )
            return None
        base_parts = parts[: len(parts) - (level - 1)]
        if node.module:
            base_parts.append(node.module)
        return ".".join(base_parts)

    def import_from(self, node: ast.ImportFrom, scope: Scope, flags: frozenset[EdgeFlag]) -> None:
        facts = self.facts
        if not node.level and node.module == "__future__":
            return
        confidence = Confidence.CERTAIN
        if node.level:
            base = self._relative_base(node)
        else:
            base, confidence = self._localize(node.module or "")
        if base is None:
            for alias in node.names:  # names are bound to something unknown
                if alias.name != "*":
                    scope.opaque.add(alias.asname or alias.name)
            return
        internal = self.index.is_internal(base)
        exists = self._resolve_absolute(base, node, flags | self._star(node), confidence)
        for alias in node.names:
            if alias.name == "*":
                facts.star_imports.append(StarImport(base=base, internal=internal, node=node))
                continue
            submodule = f"{base}.{alias.name}"
            if self.index.is_module(submodule):
                facts.import_edge(submodule, flags, confidence=confidence)
            binding = Binding(BindingKind.IMPORTED, base, alias.name, confidence=confidence)
            scope.bind(alias.asname or alias.name, binding)
            if internal and exists and not self.index.exists(submodule):
                facts.name_checks.append(
                    NameCheck(base=base, name=alias.name, node=node, source=facts.own)
                )

    @staticmethod
    def _star(node: ast.ImportFrom) -> frozenset[EdgeFlag]:
        if any(alias.name == "*" for alias in node.names):
            return frozenset({EdgeFlag.STAR_IMPORT})
        return frozenset()

    # ------------------------------------------------------------------ expressions

    def expr(self, root: ast.expr, scope: Scope, source: NodeId) -> None:
        pending = self.facts.pending
        stack: list[ast.AST] = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Call):
                pending.append(
                    Pending(kind=PendingKind.CALL, node=node, scope=scope, source=source)
                )
                chain_root, _ = chain_of(node.func)
                if not isinstance(chain_root, ast.Name):
                    stack.append(chain_root)
                stack.extend(node.args)
                stack.extend(k.value for k in node.keywords)
                continue
            if isinstance(node, ast.Name | ast.Attribute):
                ctx = node.ctx
                if isinstance(ctx, ast.Load):
                    chain_root, _ = chain_of(node)
                    if isinstance(chain_root, ast.Name):
                        pending.append(
                            Pending(
                                kind=PendingKind.REFERENCE, node=node, scope=scope, source=source
                            )
                        )
                    else:
                        stack.append(chain_root)
                elif isinstance(node, ast.Attribute):
                    pending.append(
                        Pending(kind=PendingKind.STORE, node=node, scope=scope, source=source)
                    )
                    stack.append(node.value)
                else:
                    store_name(scope, node.id)
                continue
            if isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load):
                pending.append(
                    Pending(kind=PendingKind.STORE, node=node, scope=scope, source=source)
                )
            stack.extend(ast.iter_child_nodes(node))


def walk_module(  # noqa: PLR0913  # keyword-only; each is a distinct input
    tree: ast.Module, *, path: str, module: str | None, text: str, own: NodeId, index: ModuleIndex
) -> FileFacts:
    bound, _, _ = collect_scope_names(tree.body)
    # Names a function declares ``global`` are module names too.
    declared = {
        name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names
    }
    module_scope = Scope(
        kind=ScopeKind.MODULE, qualprefix="", parent=None, source=own, bound=bound | declared
    )
    facts = FileFacts(
        path=path,
        module=module,
        text=text,
        own=own,
        module_scope=module_scope,
        is_test_file=_is_test_file(path),
    )
    walker = Walker(facts, index)
    walker.body(tree.body, module_scope, frozenset())
    if facts.all_present and not facts.all_literal:
        facts.limitations.append(
            Limitation(
                kind=LIMITATION_NON_LITERAL_ALL,
                message="__all__ is not a literal list/tuple of strings; visibility falls back "
                "to the leading-underscore rule",
                subject=path,
            )
        )
    return facts
