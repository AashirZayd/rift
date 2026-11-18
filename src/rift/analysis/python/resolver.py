"""Project-wide name resolution: pending references → edges and unresolved references.

Confidence (SPEC §9.3, ADR-0007):
- ``certain``: a name bound to a definition or import whose target is defined in the project,
  directly or through re-exports;
- ``likely``: ``self``/``cls``/``super()`` members and members inherited from a project base; a
  name with several candidate bindings; a name obtained through a star import; a literal
  ``import_module("x.y")`` (flag ``dynamic``);
- ``possible``: an attribute call whose receiver is unknown, matched by method name only.
Everything that cannot be resolved statically is recorded with an :class:`UnresolvedReason`,
never guessed (SPEC §9.4).
"""

import ast
import builtins
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from rift.analysis.python.modules import ModuleIndex
from rift.analysis.python.scopes import Binding, BindingKind, Scope, ScopeKind
from rift.analysis.python.walker import ClassFacts, FileFacts, Pending, PendingKind, chain_of
from rift.core.ids import NodeId, is_qualname, module_node_id, symbol_node_id
from rift.core.models.graph import (
    Confidence,
    Edge,
    EdgeFlag,
    EdgeKind,
    EdgeOrigin,
    UnresolvedReason,
)
from rift.core.models.source import SymbolKind

_MAX_DEPTH = 32
# Names every module/class/function can see without binding them.
_IMPLICIT = frozenset(
    {
        "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__path__",
        "__builtins__", "__annotations__", "__dict__", "__cached__", "__module__",
        "__qualname__", "__class__", "__debug__",
    }
)  # fmt: skip
_BUILTINS = frozenset(dir(builtins)) | _IMPLICIT

IMPORT_FUNCTIONS = frozenset(
    {"importlib.import_module", "importlib.__import__", "builtins.__import__"}
)
EXEC_FUNCTIONS = frozenset({"builtins.exec", "builtins.eval", "builtins.compile"})
ATTRIBUTE_FUNCTIONS = frozenset({"builtins.getattr", "builtins.setattr", "builtins.delattr"})
LOADER_FUNCTIONS = frozenset(
    {
        "runpy.run_path",
        "runpy.run_module",
        "importlib.reload",
        "importlib.util.spec_from_file_location",
        "importlib.util.module_from_spec",
        "importlib.machinery.SourceFileLoader",
        "imp.load_source",
        "imp.load_module",
    }
)
SYS_PATH_FUNCTIONS = frozenset({"site.addsitedir"})
_LIST_MUTATORS = frozenset(
    {"append", "insert", "extend", "remove", "pop", "clear", "reverse", "sort",
     "__setitem__", "__delitem__", "__iadd__"}
)  # fmt: skip
NAMESPACE_FUNCTIONS = frozenset({"builtins.globals", "builtins.locals", "builtins.vars"})
_DICT_MUTATORS = frozenset(
    {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}
)


class Outcome(StrEnum):
    FOUND = "found"
    OPAQUE = "opaque"  # bound, but not to something statically understood
    EXTERNAL = "external"
    MISSING = "missing"
    GETATTR = "getattr"  # the module defines __getattr__
    STAR_UNKNOWN = "star_unknown"  # may come from a star import whose names are unknown
    UNPARSED = "unparsed"  # the module exists but could not be analyzed


# Outcomes that end resolution without guessing: the import itself was already recorded.
_SETTLED = frozenset({Outcome.EXTERNAL, Outcome.MISSING, Outcome.UNPARSED, Outcome.GETATTR})


class LookupStatus(StrEnum):
    BOUND = "bound"  # has statically understood bindings
    OPAQUE = "opaque"  # a local, parameter or plain assignment
    BUILTIN = "builtin"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Target:
    """A project node: a module (``qualname is None``) or a symbol in it."""

    module: str
    qualname: str | None
    confidence: Confidence
    via_star: bool = False

    def node_id(self) -> NodeId | None:
        if self.qualname is None:
            return module_node_id(self.module)
        if not is_qualname(self.qualname):
            return None
        return symbol_node_id(self.module, self.qualname)

    def weaker(self, confidence: Confidence) -> "Target":
        return Target(self.module, self.qualname, min(self.confidence, confidence), self.via_star)

    def then(self, nxt: "Target") -> "Target":
        """Continue a chain: the weakest link and any star import carry over."""
        return Target(
            nxt.module,
            nxt.qualname,
            min(self.confidence, nxt.confidence),
            self.via_star or nxt.via_star,
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    outcome: Outcome
    targets: tuple[Target, ...] = ()


@dataclass(frozen=True, slots=True)
class Lookup:
    """Where a bare name came from, seen from some scope."""

    status: LookupStatus
    scope: Scope | None = None
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True, slots=True)
class Chain:
    targets: tuple[Target, ...]  # deepest resolved node per candidate binding
    complete: bool  # every attribute of the chain resolved
    lookup: Lookup | None  # None: the chain root is not a name
    outcome: Outcome  # FOUND, or why the root did not resolve


class Resolver:
    """Shared, read-only view of every module's facts."""

    def __init__(self, index: ModuleIndex, facts: Iterable[FileFacts]) -> None:
        self.index = index
        self.facts: dict[str, FileFacts] = {f.module: f for f in facts if f.module is not None}
        methods: dict[str, list[NodeId]] = {}
        for module, file_facts in sorted(self.facts.items()):
            for qualname, draft in sorted(file_facts.symbols.items()):
                if draft.kind in {SymbolKind.METHOD, SymbolKind.TEST_METHOD}:
                    node_id = Target(module, qualname, Confidence.POSSIBLE).node_id()
                    if node_id is not None:
                        methods.setdefault(draft.name, []).append(node_id)
        self._methods = {name: tuple(sorted(ids)) for name, ids in methods.items()}
        self._base_cache: dict[tuple[str, str], tuple[Target, ...]] = {}

    # ------------------------------------------------------------------ module members

    def member(  # noqa: PLR0911  # one exit per step of Python's attribute lookup order
        self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Resolution:
        """Resolve ``module.name`` the way attribute access / ``from module import name`` does:
        the module's own bindings first, then a submodule, then ``__getattr__``/star imports."""
        if not self.index.is_internal(module):
            return Resolution(Outcome.EXTERNAL)
        key = (module, name)
        facts = self.facts.get(module)
        submodule = f"{module}.{name}"
        if facts is not None and key not in seen and len(seen) < _MAX_DEPTH:
            bindings = facts.module_scope.bindings.get(name)
            if bindings:
                return self._bindings(facts, bindings, name, seen | {key})
            if name in facts.module_scope.bound and not self.index.exists(submodule):
                return Resolution(Outcome.OPAQUE)
        if self.index.is_module(submodule):
            return Resolution(Outcome.FOUND, (Target(submodule, None, Confidence.CERTAIN),))
        if self.index.is_namespace(submodule):
            return Resolution(Outcome.OPAQUE)
        if facts is None:
            return Resolution(Outcome.UNPARSED if self.index.is_module(module) else Outcome.MISSING)
        if facts.has_getattr:
            return Resolution(Outcome.GETATTR)
        if facts.star_imports:
            return self.via_star(facts, name, seen | {key})
        return Resolution(Outcome.MISSING)

    def _bindings(
        self,
        facts: FileFacts,
        bindings: list[Binding],
        name: str,
        seen: frozenset[tuple[str, str]],
    ) -> Resolution:
        targets: list[Target] = []
        outcome = Outcome.OPAQUE
        for binding in bindings:
            resolved = self.binding(facts, binding, seen)
            if resolved.outcome is Outcome.FOUND:
                outcome = Outcome.FOUND
                targets += resolved.targets
            elif outcome is not Outcome.FOUND:
                outcome = resolved.outcome
        if facts.is_ambiguous(name, len(bindings)):
            targets = [t.weaker(Confidence.LIKELY) for t in targets]
        return Resolution(outcome, tuple(targets))

    def binding(
        self, facts: FileFacts, binding: Binding, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Resolution:
        """Resolve one binding made in ``facts``' module."""
        if binding.kind is BindingKind.DEF:
            if facts.module is None:
                return Resolution(Outcome.OPAQUE)
            target = Target(facts.module, binding.target, Confidence.CERTAIN)
            return Resolution(Outcome.FOUND, (target,))
        if binding.kind is BindingKind.MODULE:
            if self.index.exists(binding.target):
                return Resolution(
                    Outcome.FOUND, (Target(binding.target, None, Confidence.CERTAIN),)
                )
            internal = self.index.is_internal(binding.target)
            return Resolution(Outcome.MISSING if internal else Outcome.EXTERNAL)
        return self.member(binding.target, binding.attr, seen)

    def star_determinable(self, module: str) -> bool:
        """Can the names ``from module import *`` binds be listed statically?"""
        facts = self.facts.get(module)
        return facts is not None and facts.all_literal and not facts.star_imports

    def exported(self, module: str, name: str) -> bool:
        """Whether ``from module import *`` binds ``name`` (module must be determinable)."""
        facts = self.facts[module]
        if facts.all_present:
            return name in facts.all_names
        return not name.startswith("_") and name in facts.module_scope.bound

    def via_star(
        self, facts: FileFacts, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Resolution:
        targets: list[Target] = []
        unknown = False
        for star in facts.star_imports:
            if not star.internal or not self.star_determinable(star.base):
                unknown = True
                if not star.internal:
                    continue
            elif not self.exported(star.base, name):
                continue
            resolved = self.member(star.base, name, seen)
            targets += [
                Target(t.module, t.qualname, Confidence.LIKELY, via_star=True)
                for t in resolved.targets
            ]
        if targets:
            return Resolution(Outcome.FOUND, tuple(targets))
        return Resolution(Outcome.STAR_UNKNOWN if unknown else Outcome.MISSING)

    # ------------------------------------------------------------------ classes

    def class_bases(self, module: str, qualname: str) -> tuple[Target, ...]:
        key = (module, qualname)
        cached = self._base_cache.get(key)
        if cached is not None:
            return cached
        self._base_cache[key] = ()  # cycle guard while resolving
        facts = self.facts.get(module)
        cls = facts.classes.get(qualname) if facts is not None else None
        bases: list[Target] = []
        if facts is not None and cls is not None:
            for base in cls.bases:
                chain = self.chain(facts, base, cls.scope)
                if chain.complete:
                    bases += [t for t in chain.targets if t.qualname is not None]
        result = tuple(bases)
        self._base_cache[key] = result
        return result

    def class_member(
        self, module: str, qualname: str, name: str, *, skip_self: bool = False
    ) -> tuple[bool, tuple[Target, ...]]:
        """Look ``name`` up along the project class hierarchy (breadth-first, cycle-safe).
        Returns ``(found, targets)``; ``found`` with no targets means a data attribute."""
        queue: list[tuple[str, str, bool]] = [(module, qualname, skip_self)]
        visited: set[tuple[str, str]] = set()
        while queue and len(visited) < _MAX_DEPTH:
            mod, qual, skip = queue.pop(0)
            if (mod, qual) in visited:
                continue
            visited.add((mod, qual))
            facts = self.facts.get(mod)
            cls: ClassFacts | None = facts.classes.get(qual) if facts is not None else None
            if facts is None or cls is None:
                continue
            if not skip and name in cls.members:
                member = f"{qual}.{name}"
                if member not in facts.symbols:
                    return True, ()
                own = (mod, qual) == (module, qualname)
                confidence = Confidence.CERTAIN if own else Confidence.LIKELY
                return True, (Target(mod, member, confidence),)
            queue += [
                (t.module, t.qualname, False) for t in self.class_bases(mod, qual) if t.qualname
            ]
        return False, ()

    # ------------------------------------------------------------------ scopes and chains

    @staticmethod
    def lookup(name: str, scope: Scope) -> Lookup:
        """Python's scoping: local, enclosing functions (classes skipped), module, builtins."""
        current: Scope | None = scope
        skip_class = False
        while current is not None:
            if current.kind is ScopeKind.CLASS and skip_class:
                current = current.parent
                continue
            if current.kind is ScopeKind.MODULE:
                if name in current.bindings:
                    return Lookup(LookupStatus.BOUND, current, tuple(current.bindings[name]))
                if name in current.bound:
                    return Lookup(LookupStatus.OPAQUE, current)
                break
            if name in current.declared_global:
                current = current.module()
                continue
            if name in current.bindings:
                return Lookup(LookupStatus.BOUND, current, tuple(current.bindings[name]))
            if name in current.bound:
                return Lookup(LookupStatus.OPAQUE, current)
            current = current.parent
            skip_class = True
        if name in _BUILTINS:
            return Lookup(LookupStatus.BUILTIN)
        return Lookup(LookupStatus.UNKNOWN)

    def qualified(self, expr: ast.expr, scope: Scope) -> str | None:
        """Fully qualified dotted name of a module-rooted expression (``importlib.import_module``
        however it was imported), used to recognise dynamic APIs."""
        root, attrs = chain_of(expr)
        if not isinstance(root, ast.Name):
            return None
        found = self.lookup(root.id, scope)
        if found.status is LookupStatus.BUILTIN:
            return ".".join(["builtins", root.id, *attrs])
        for binding in found.bindings:
            if binding.kind is BindingKind.MODULE:
                return ".".join([binding.target, *attrs])
            if binding.kind is BindingKind.IMPORTED:
                return ".".join([binding.target, binding.attr, *attrs])
        return None

    def chain(self, facts: FileFacts, expr: ast.expr, scope: Scope) -> Chain:
        """Resolve ``a.b.c`` as far as possible from ``scope`` in ``facts``' module."""
        root, attrs = chain_of(expr)
        if not isinstance(root, ast.Name):
            return Chain((), False, None, Outcome.OPAQUE)
        found = self.lookup(root.id, scope)
        if found.status is LookupStatus.BOUND:
            module_level = found.scope is not None and found.scope.kind is ScopeKind.MODULE
            start = self._bindings(
                facts,
                list(found.bindings),
                root.id if module_level else "",
                frozenset(),
            )
        elif found.status is LookupStatus.UNKNOWN and facts.star_imports:
            start = self.via_star(facts, root.id)
        else:
            return Chain((), False, found, Outcome.OPAQUE)
        if not start.targets:
            return Chain((), False, found, start.outcome)
        results: list[Target] = []
        complete = True
        for target in start.targets:
            deepest, done = self._descend(target, attrs)
            results.append(deepest)
            complete = complete and done
        return Chain(tuple(results), complete, found, Outcome.FOUND)

    def _descend(self, start: Target, attrs: list[str]) -> tuple[Target, bool]:
        current = start
        for attr in attrs:
            if current.qualname is None:
                resolved = self.member(current.module, attr)
                if resolved.outcome is not Outcome.FOUND or not resolved.targets:
                    return current, False
                current = current.then(resolved.targets[0])
                continue
            facts = self.facts.get(current.module)
            draft = facts.symbols.get(current.qualname) if facts is not None else None
            if draft is None or draft.kind is not SymbolKind.CLASS:
                return current, False
            found, members = self.class_member(current.module, current.qualname, attr)
            if not found or not members:
                return current, False
            current = current.then(members[0])
        return current, True

    def name_match(self, name: str) -> tuple[NodeId, ...]:
        return self._methods.get(name, ())


# ---------------------------------------------------------------------- per-file resolution


class FileResolver:
    """Turns one file's pending items into edges and unresolved references."""

    def __init__(self, resolver: Resolver, facts: FileFacts) -> None:
        self.r = resolver
        self.facts = facts

    def run(self) -> None:
        facts = self.facts
        for check in facts.name_checks:
            outcome = self.r.member(check.base, check.name).outcome
            reason = {
                Outcome.MISSING: UnresolvedReason.MISSING_MODULE,
                Outcome.GETATTR: UnresolvedReason.MODULE_GETATTR,
                Outcome.STAR_UNKNOWN: UnresolvedReason.STAR_IMPORT,
            }.get(outcome)
            if reason is not None:
                facts.unresolved_at(check.node, check.source, reason, f"{check.base}.{check.name}")
        for star in facts.star_imports:
            if not star.internal or not self.r.star_determinable(star.base):
                facts.unresolved_at(
                    star.node, facts.own, UnresolvedReason.STAR_IMPORT, f"{star.base}.*"
                )
        for item in facts.pending:
            self._pending(item)

    def _edge(
        self,
        source: NodeId,
        target: Target,
        kind: EdgeKind,
        *,
        origin: EdgeOrigin = EdgeOrigin.NAME_RESOLUTION,
    ) -> None:
        if target.qualname is None and not self.r.index.is_module(target.module):
            return  # a namespace package has no file and so no node
        node_id = target.node_id()
        if node_id is None:
            return
        flags = frozenset({EdgeFlag.STAR_IMPORT}) if target.via_star else frozenset()
        self.facts.edges.append(
            Edge(
                source=source,
                target=node_id,
                kind=kind,
                origin=origin,
                confidence=target.confidence,
                evidence_ids=(),
                flags=flags,
            )
        )

    def _name_match(self, name: str, source: NodeId) -> None:
        for node_id in self.r.name_match(name):
            self.facts.edges.append(
                Edge(
                    source=source,
                    target=node_id,
                    kind=EdgeKind.CALLS,
                    origin=EdgeOrigin.HEURISTIC,
                    confidence=Confidence.POSSIBLE,
                    evidence_ids=(),
                    flags=frozenset(),
                )
            )

    def _record(self, node: ast.AST, source: NodeId, reason: UnresolvedReason) -> None:
        self.facts.unresolved_at(node, source, reason, self.facts.raw(node))

    def _pending(self, item: Pending) -> None:
        node = item.node
        if item.kind is PendingKind.CALL and isinstance(node, ast.Call):
            self._call(node, item.scope, item.source)
        elif item.kind is PendingKind.STORE:
            self._store(node, item.scope, item.source)
        elif item.kind is PendingKind.BASE:
            self._reference(node, item.scope, item.source, EdgeKind.INHERITS)
        else:  # REFERENCE, DECORATOR
            self._reference(node, item.scope, item.source, EdgeKind.REFERENCES)

    def _unknown_name(self, expr: ast.expr, chain: Chain, source: NodeId) -> None:
        """A name bound nowhere visible: ``star_import`` if a star import whose names are
        unknown could have bound it, else ``unknown``."""
        if chain.lookup is None or chain.lookup.status is not LookupStatus.UNKNOWN:
            return
        star = chain.outcome is Outcome.STAR_UNKNOWN
        reason = UnresolvedReason.STAR_IMPORT if star else UnresolvedReason.UNKNOWN
        root, attrs = chain_of(expr)
        name = root.id if isinstance(root, ast.Name) else "?"
        self.facts.unresolved_at(expr, source, reason, ".".join([name, *attrs]))

    def _reference(self, expr: ast.expr, scope: Scope, source: NodeId, kind: EdgeKind) -> None:
        chain = self.r.chain(self.facts, expr, scope)
        for target in chain.targets:
            self._edge(source, target, kind)
        if not chain.targets and not self._self_member(expr, scope, source, kind, call=False):
            self._unknown_name(expr, chain, source)

    def _self_member(
        self, expr: ast.expr, scope: Scope, source: NodeId, kind: EdgeKind, *, call: bool
    ) -> bool:
        """``self.m`` / ``cls.m`` inside a method: members of the class hierarchy (likely).
        Returns whether ``expr`` was such an access (handled, with or without edges)."""
        root, attrs = chain_of(expr)
        if not isinstance(root, ast.Name) or not attrs or self.facts.module is None:
            return False
        owner = self.r.lookup(root.id, scope).scope
        if owner is None or owner.self_name != root.id or owner.class_qualname is None:
            return False
        found, members = self.r.class_member(self.facts.module, owner.class_qualname, attrs[0])
        if found and members and len(attrs) == 1:
            for member in members:
                self._edge(source, member.weaker(Confidence.LIKELY), kind)
        elif call:
            self._name_match(attrs[-1], source)
        return True

    def _dynamic_call(self, call: ast.Call, qualified: str | None, source: NodeId) -> bool:
        """Calls of dynamic APIs (SPEC §9.4); returns whether ``call`` was one."""
        if qualified in EXEC_FUNCTIONS:
            self._record(call, source, UnresolvedReason.EXEC_EVAL)
        elif qualified in LOADER_FUNCTIONS:
            self._record(call, source, UnresolvedReason.DYNAMIC_IMPORT)
        elif qualified in SYS_PATH_FUNCTIONS or (
            qualified is not None
            and qualified.startswith("sys.path.")
            and qualified.count(".") == 2  # noqa: PLR2004  # sys.path.<method>
            and qualified.rsplit(".", 1)[-1] in _LIST_MUTATORS
        ):
            self._record(call, source, UnresolvedReason.SYS_PATH_MUTATION)
        else:
            return False
        return True

    def _call(self, call: ast.Call, scope: Scope, source: NodeId) -> None:
        if not self._special_call(call, scope, source):
            self._plain_call(call.func, scope, source)

    def _special_call(self, call: ast.Call, scope: Scope, source: NodeId) -> bool:
        """Dynamic APIs (SPEC §9.4) and ``super()``; returns whether ``call`` was one."""
        func = call.func
        qualified = self.r.qualified(func, scope)
        if qualified in IMPORT_FUNCTIONS:
            self._dynamic_import(call, scope, source)
            return True
        if qualified in ATTRIBUTE_FUNCTIONS:
            self._attribute_call(call, qualified or "", scope, source)
            return True
        if self._dynamic_call(call, qualified, source):
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            inner = self.r.qualified(func.value.func, scope)
            if inner in NAMESPACE_FUNCTIONS and func.attr in _DICT_MUTATORS:
                self._record(call, source, UnresolvedReason.DYNAMIC_ATTRIBUTE)
                return True
            if inner == "builtins.super":
                self._super_call(func.attr, scope, source)
                return True
        return False

    def _plain_call(self, func: ast.expr, scope: Scope, source: NodeId) -> None:
        root, attrs = chain_of(func)
        if not isinstance(root, ast.Name):
            if attrs:
                self._call_on_result(root, attrs, scope, source)
            return
        chain = self.r.chain(self.facts, func, scope)
        if chain.complete:
            for target in chain.targets:
                if target.qualname is not None:
                    self._edge(source, target, EdgeKind.CALLS)
        elif chain.targets:  # partly resolved: the receiver is still a dependency
            for target in chain.targets:
                self._edge(source, target, EdgeKind.REFERENCES)
            if any(t.qualname is not None for t in chain.targets):
                self._name_match(attrs[-1], source)
        elif self._self_member(func, scope, source, EdgeKind.CALLS, call=True):
            return
        elif chain.lookup is not None and chain.lookup.status is LookupStatus.UNKNOWN:
            self._unknown_name(func, chain, source)
        elif (
            attrs
            and chain.outcome not in _SETTLED
            and chain.lookup is not None
            and chain.lookup.status is not LookupStatus.BUILTIN
        ):
            self._name_match(attrs[-1], source)  # a local / parameter receiver

    def _call_on_result(
        self, receiver: ast.expr, attrs: list[str], scope: Scope, source: NodeId
    ) -> None:
        """``Engine().go()``: an instance of a project class is ``likely``; any other receiver
        (``make().run()``) is unknown, so the method is matched by name (``possible``)."""
        if isinstance(receiver, ast.Call) and len(attrs) == 1:
            chain = self.r.chain(self.facts, receiver.func, scope)
            classes = [
                t
                for t in chain.targets
                if chain.complete and t.qualname is not None and self._is_class(t)
            ]
            members = [
                m
                for cls in classes
                for m in self.r.class_member(cls.module, cls.qualname or "", attrs[0])[1]
            ]
            if members:
                for member in members:
                    self._edge(source, member.weaker(Confidence.LIKELY), EdgeKind.CALLS)
                return
        self._name_match(attrs[-1], source)

    def _is_class(self, target: Target) -> bool:
        facts = self.r.facts.get(target.module)
        draft = facts.symbols.get(target.qualname or "") if facts is not None else None
        return draft is not None and draft.kind is SymbolKind.CLASS

    def _super_call(self, name: str, scope: Scope, source: NodeId) -> None:
        current: Scope | None = scope
        while current is not None and current.class_qualname is None:
            current = current.parent
        if current is None or current.class_qualname is None or self.facts.module is None:
            self._name_match(name, source)
            return
        found, members = self.r.class_member(
            self.facts.module, current.class_qualname, name, skip_self=True
        )
        for member in members:
            self._edge(source, member.weaker(Confidence.LIKELY), EdgeKind.CALLS)
        if not found:  # e.g. inherited from an external base
            self._name_match(name, source)

    def _attribute_call(self, call: ast.Call, qualified: str, scope: Scope, source: NodeId) -> None:
        name_arg = call.args[1] if len(call.args) > 1 else None
        if name_arg is None:
            name_arg = next((k.value for k in call.keywords if k.arg == "name"), None)
        literal = isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)
        if not literal:
            self._record(call, source, UnresolvedReason.DYNAMIC_ATTRIBUTE)
        elif qualified != "builtins.getattr" and call.args and self._is_module(call.args[0], scope):
            self._record(call, source, UnresolvedReason.DYNAMIC_ATTRIBUTE)  # monkeypatching
        for arg in call.args[:1]:
            self._reference(arg, scope, source, EdgeKind.REFERENCES)

    def _is_module(self, expr: ast.expr, scope: Scope) -> bool:
        """Does ``expr`` statically denote a module (project or external)?"""
        root, attrs = chain_of(expr)
        if not isinstance(root, ast.Name):
            return False
        lookup = self.r.lookup(root.id, scope)
        if lookup.status is not LookupStatus.BOUND:
            return False
        if not attrs and any(b.kind is BindingKind.MODULE for b in lookup.bindings):
            return True
        chain = self.r.chain(self.facts, expr, scope)
        return (
            chain.complete
            and bool(chain.targets)
            and all(t.qualname is None for t in chain.targets)
        )

    def _store(self, target: ast.expr, scope: Scope, source: NodeId) -> None:
        if isinstance(target, ast.Subscript):
            value = target.value
            if (
                isinstance(value, ast.Call)
                and self.r.qualified(value.func, scope) in NAMESPACE_FUNCTIONS
            ):
                self._record(target, source, UnresolvedReason.DYNAMIC_ATTRIBUTE)
            elif self.r.qualified(value, scope) == "sys.path":
                self._record(target, source, UnresolvedReason.SYS_PATH_MUTATION)
        elif isinstance(target, ast.Attribute):
            if self.r.qualified(target, scope) == "sys.path":
                self._record(target, source, UnresolvedReason.SYS_PATH_MUTATION)
            elif self._is_module(target.value, scope):
                self._record(target, source, UnresolvedReason.DYNAMIC_ATTRIBUTE)  # monkeypatching

    def _dynamic_import(self, call: ast.Call, scope: Scope, source: NodeId) -> None:
        facts = self.facts
        name_arg = call.args[0] if call.args else None
        if name_arg is None:
            name_arg = next((k.value for k in call.keywords if k.arg == "name"), None)
        if not (
            isinstance(name_arg, ast.Constant)
            and isinstance(name_arg.value, str)
            and name_arg.value
        ):
            self._record(call, source, UnresolvedReason.DYNAMIC_IMPORT)
            return
        name: str | None = name_arg.value
        if name is not None and name.startswith("."):
            name = self._relative_literal(call, name, scope)
        if name is None:
            self._record(call, source, UnresolvedReason.DYNAMIC_IMPORT)
            return
        index = self.r.index
        if index.is_module(name):
            facts.import_edge(name, frozenset({EdgeFlag.DYNAMIC}), confidence=Confidence.LIKELY)
        elif index.is_namespace(name):
            return
        elif index.is_internal(name):
            facts.unresolved_at(call, source, UnresolvedReason.MISSING_MODULE, name)
        else:
            facts.unresolved_at(call, source, UnresolvedReason.EXTERNAL_MODULE, name)

    def _relative_literal(self, call: ast.Call, name: str, scope: Scope) -> str | None:
        """``import_module(".x", package=...)`` with a literal package or ``__package__``."""
        facts = self.facts
        package_arg = call.args[1] if len(call.args) > 1 else None
        if package_arg is None:
            package_arg = next((k.value for k in call.keywords if k.arg == "package"), None)
        package: str | None = None
        if isinstance(package_arg, ast.Constant) and isinstance(package_arg.value, str):
            package = package_arg.value
        elif (
            isinstance(package_arg, ast.Name)
            and package_arg.id == "__package__"
            and self.r.lookup("__package__", scope).status is LookupStatus.BUILTIN
            and facts.module is not None
        ):
            package = self.r.index.package_of(facts.module)
        if not package:
            return None
        level = len(name) - len(name.lstrip("."))
        parts = package.split(".")
        if level - 1 >= len(parts):
            return None
        rest = name[level:]
        base = parts[: len(parts) - (level - 1)]
        return ".".join([*base, rest] if rest else base)
