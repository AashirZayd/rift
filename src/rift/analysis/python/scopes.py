"""Lexical scopes and name bindings (ADR-0007).

Scopes follow Python's rules (module, class, function; class scopes are invisible to nested
functions; ``global``/``nonlocal`` redirect). Lambdas and comprehensions are folded into the
enclosing scope: their parameters and targets count as local there. That over-approximation can
only hide a resolution (an extra local), never invent an edge.
"""

import ast
from dataclasses import dataclass, field
from enum import StrEnum

from rift.core.ids import NodeId
from rift.core.models.graph import Confidence


class BindingKind(StrEnum):
    DEF = "def"  # a def/class in the analyzed module: ``target`` is its qualname
    MODULE = "module"  # ``import x.y [as z]``: ``target`` is the module name bound
    IMPORTED = "imported"  # ``from m import n``: ``target`` = absolute module, ``attr`` = n


@dataclass(frozen=True, slots=True)
class Binding:
    kind: BindingKind
    target: str
    attr: str = ""
    # ``likely`` for an import resolved by pytest-style sys.path insertion (ADR-0008).
    confidence: Confidence = Confidence.CERTAIN


class ScopeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"


@dataclass(eq=False, slots=True, kw_only=True)
class Scope:
    """``bound``: every name bound in this scope (for the module: every module-level name).
    ``bindings``: the statically understood bindings, in source order; a bound name without one
    is opaque (an assignment, loop target, ...)."""

    kind: ScopeKind
    qualprefix: str  # "" (module), "C." (class C), "f.<locals>." (function f)
    parent: "Scope | None"
    source: NodeId  # node that edges from code in this scope start at
    bound: frozenset[str]
    declared_global: frozenset[str] = frozenset()
    declared_nonlocal: frozenset[str] = frozenset()
    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    # Names (also) bound by a plain store: assignment, loop/with/except/match target, del.
    opaque: set[str] = field(default_factory=set)
    class_qualname: str | None = None  # set on a method's scope (and on a class scope)
    self_name: str | None = None  # first parameter of a method (not for staticmethods)
    is_classmethod: bool = False  # self_name is bound to the class, not an instance

    def bind(self, name: str, binding: Binding) -> None:
        existing = self.bindings.setdefault(name, [])
        if binding not in existing:
            existing.append(binding)

    def module(self) -> "Scope":
        scope = self
        while scope.parent is not None:
            scope = scope.parent
        return scope


def _arg_names(args: ast.arguments) -> list[str]:
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg is not None:
        every.append(args.vararg)
    if args.kwarg is not None:
        every.append(args.kwarg)
    return [a.arg for a in every]


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _type_param_names(node: ast.AST) -> list[str]:
    """PEP 695 type parameters (3.12+ ``type_params``); absent on older interpreters."""
    params = getattr(node, "type_params", None) or []
    return [p.name for p in params if isinstance(getattr(p, "name", None), str)]


def collect_scope_names(  # noqa: PLR0912  # flat dispatch over binding node types
    body: list[ast.stmt], extra: list[str] | None = None
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """``(bound, global, nonlocal)`` names of the scope whose statements are ``body``.

    Iterative; descends into everything except the bodies of nested defs and classes (their
    names, decorators, defaults and bases belong to this scope)."""
    bound: set[str] = set(extra or ())
    declared_global: set[str] = set()
    declared_nonlocal: set[str] = set()
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            bound.add(node.name)
            bound.update(_type_param_names(node))
            stack.extend(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                stack.extend(node.bases)
                stack.extend(k.value for k in node.keywords)
            else:
                stack.extend(d for d in node.args.defaults)
                stack.extend(d for d in node.args.kw_defaults if d is not None)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bound.add(node.id)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            declared_nonlocal.update(node.names)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, ast.Lambda):
            bound.update(_arg_names(node.args))
        elif type(node).__name__ == "TypeAlias":
            bound.update(_type_param_names(node))
        stack.extend(ast.iter_child_nodes(node))
    bound -= declared_global | declared_nonlocal
    return frozenset(bound), frozenset(declared_global), frozenset(declared_nonlocal)


def function_extra_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [*_arg_names(node.args), *_type_param_names(node)]


def class_extra_names(node: ast.ClassDef) -> list[str]:
    return _type_param_names(node)
