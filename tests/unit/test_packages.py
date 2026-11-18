"""Every SPEC §4 subpackage exists, imports cleanly, and holds no feature logic in M0."""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import rift

SUBPACKAGES = (
    "rift.analysis",
    "rift.analysis.python",
    "rift.cache",
    "rift.cli",
    "rift.config",
    "rift.core",
    "rift.core.models",
    "rift.discovery",
    "rift.evidence",
    "rift.git",
    "rift.graph",
    "rift.impact",
    "rift.invariants",
    "rift.plugins",
    "rift.policy",
    "rift.proof",
    "rift.services",
    "rift.verification",
)

TOP_LEVEL = frozenset(name.removeprefix("rift.") for name in SUBPACKAGES if name.count(".") == 1)


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_and_states_responsibility(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__ is not None
    assert module.__doc__.strip(), f"{name} needs a docstring stating its single responsibility"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_init_is_docstring_only(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    assert len(tree.body) == 1, f"{name}/__init__.py must contain only its docstring"
    (node,) = tree.body
    assert isinstance(node, ast.Expr)
    assert isinstance(node.value, ast.Constant)
    assert isinstance(node.value.value, str)


def test_no_unlisted_top_level_subpackages() -> None:
    found = frozenset(info.name for info in pkgutil.iter_modules(rift.__path__) if info.ispkg)
    assert found == TOP_LEVEL


def test_root_package_holds_only_version() -> None:
    assert rift.__file__ is not None
    tree = ast.parse(Path(rift.__file__).read_text(encoding="utf-8"))
    kinds = [type(node).__name__ for node in tree.body]
    assert kinds == ["Expr", "Assign"]
    assign = tree.body[1]
    assert isinstance(assign, ast.Assign)
    assert [ast.unparse(t) for t in assign.targets] == ["__version__"]


def test_plugin_api_module_exists_without_logic() -> None:
    api = importlib.import_module("rift.plugins.api")
    assert api.__doc__ is not None
    assert api.__file__ is not None
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    assert len(tree.body) == 1


def _imported_modules(path: Path, package: str) -> list[str]:
    """Absolute names of every module a source file imports (relative imports resolved)."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                names.append(".".join([*base, node.module] if node.module else base))
            else:
                names.append(node.module or "")
    return names


def test_core_never_imports_the_root_package() -> None:
    # import-linter cannot forbid the ancestor package `rift` itself (ADR-0002), so pin it here:
    # core may import only rift.core.* from rift.
    core_dir = Path(rift.__path__[0]) / "core"
    offenders = []
    for path in sorted(core_dir.rglob("*.py")):
        rel = path.relative_to(core_dir.parent.parent).with_suffix("")
        parts = rel.parts if rel.name != "__init__" else rel.parts[:-1]
        package = ".".join(parts if path.name == "__init__.py" else parts[:-1])
        for name in _imported_modules(path, package):
            if (name == "rift" or name.startswith("rift.")) and not (
                name == "rift.core" or name.startswith("rift.core.")
            ):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []
