"""Module naming and the project module index (SPEC §9.3, ADR-0007).

Files under a configured source root are named relative to it. Files outside every source root
(tests, scripts next to a ``src/`` layout) are named relative to the repository root as a
fallback, so their imports can still be resolved. A name owned by two files goes to the
higher-priority one: configured root before fallback, a package ``__init__.py`` before a
same-named ``.py`` (as CPython's path finder does), then path order. The loser is unnamed.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rift.discovery.source_roots import REPO_ROOT, importable_name


@dataclass(frozen=True, slots=True, kw_only=True)
class ModuleFile:
    name: str
    path: str
    is_package: bool  # an ``__init__.py``


@dataclass(frozen=True, slots=True, kw_only=True)
class Shadowed:
    path: str
    name: str
    winner: str


class ModuleIndex:
    """Immutable after construction. ``namespace`` packages are directories that only appear as
    a prefix of some module name and have no ``__init__.py``."""

    def __init__(self, files: Sequence[str], source_roots: Sequence[str]) -> None:
        roots = sorted((r for r in source_roots if r != REPO_ROOT), key=lambda r: (-len(r), r))
        has_repo_root = REPO_ROOT in source_roots
        candidates: list[tuple[int, int, str, str]] = []  # (tier, not-package, path, name)
        for path in sorted(files):
            rel, tier = _relative(path, roots, has_repo_root)
            name = importable_name(rel) if rel is not None else None
            if name is not None:
                is_pkg = rel is not None and (rel == "__init__.py" or rel.endswith("/__init__.py"))
                candidates.append((tier, 0 if is_pkg else 1, path, name))
        owners: dict[str, ModuleFile] = {}
        shadowed: list[Shadowed] = []
        for _tier, not_pkg, path, name in sorted(candidates):
            if name in owners:
                shadowed.append(Shadowed(path=path, name=name, winner=owners[name].path))
                continue
            owners[name] = ModuleFile(name=name, path=path, is_package=not_pkg == 0)
        self._modules = owners
        self._by_path = {info.path: info.name for info in owners.values()}
        self.shadowed: tuple[Shadowed, ...] = tuple(sorted(shadowed, key=lambda s: s.path))
        prefixes: set[str] = set()
        for name in owners:
            parts = name.split(".")
            prefixes.update(".".join(parts[:i]) for i in range(1, len(parts)))
        self._namespaces = frozenset(p for p in prefixes if p not in owners)
        self._top_level = frozenset(name.split(".")[0] for name in owners)

    def module_of(self, path: str) -> str | None:
        return self._by_path.get(path)

    def file_of(self, name: str) -> ModuleFile | None:
        return self._modules.get(name)

    def is_module(self, name: str) -> bool:
        """A module or regular package with a file."""
        return name in self._modules

    def is_namespace(self, name: str) -> bool:
        return name in self._namespaces

    def exists(self, name: str) -> bool:
        return name in self._modules or name in self._namespaces

    def is_internal(self, name: str) -> bool:
        """The top-level package is part of this project (so a miss is ``missing_module``)."""
        return name.split(".", maxsplit=1)[0] in self._top_level

    def package_of(self, name: str) -> str:
        """The package relative imports resolve against (``""`` for a top-level module)."""
        info = self._modules.get(name)
        if info is not None and info.is_package:
            return name
        return name.rpartition(".")[0]

    def regular_ancestors(self, name: str) -> tuple[str, ...]:
        """Proper prefixes of ``name`` that are regular packages (their ``__init__`` runs)."""
        parts = name.split(".")
        found = (".".join(parts[:i]) for i in range(1, len(parts)))
        return tuple(p for p in found if p in self._modules)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._modules))


def _relative(path: str, roots: Sequence[str], has_repo_root: bool) -> tuple[str | None, int]:
    for root in roots:
        if path.startswith(root + "/"):
            return path[len(root) + 1 :], 0
    return path, (0 if has_repo_root else 1)
