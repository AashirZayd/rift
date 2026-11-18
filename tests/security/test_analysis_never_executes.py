"""SPEC §21.2 passive code execution: the analyzer never imports, executes or compiles analyzed
code. Canary files write a marker when imported or run; analysis must leave no marker, no
bytecode cache and no trace in ``sys.modules``. Analysis runs FIRST, then a plain import in a
subprocess is the positive control proving each canary really fires.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import rift
from rift.analysis.python.analyzer import analyze_project
from tests.fixtures.python_project import write_project

pytestmark = pytest.mark.security

CANARY_MODULES = (
    "canarypkg",
    "canarypkg.mod",
    "conftest",
    "sitecustomize",
    "usercustomize",
    "setup",
)


def _canary(marker: Path, name: str) -> str:
    return (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).joinpath({name!r}).write_text('ran', encoding='utf-8')\n"
        "def helper():\n"
        "    return 1\n"
    )


def _project(tmp_path: Path) -> tuple[Path, Path]:
    markers = tmp_path / "markers"
    markers.mkdir()
    root = write_project(
        tmp_path / "repo",
        {
            "canarypkg/__init__.py": _canary(markers, "pkg-init"),
            "canarypkg/mod.py": _canary(markers, "pkg-mod")
            + "import importlib\nimportlib.import_module('canarypkg')\n__import__('canarypkg')\n"
            + "exec('VALUE = 1')\n",
            "conftest.py": _canary(markers, "conftest"),
            "sitecustomize.py": _canary(markers, "sitecustomize"),
            "usercustomize.py": _canary(markers, "usercustomize"),
            "setup.py": _canary(markers, "setup"),
            "tests/test_canary.py": _canary(markers, "test-module")
            + "from canarypkg.mod import helper\ndef test_x():\n    helper()\n",
        },
    )
    return root, markers


def test_analysis_never_runs_repository_code(tmp_path: Path) -> None:
    root, markers = _project(tmp_path)
    before = set(sys.modules)

    project = analyze_project(root)

    assert len(project.results) == 7
    assert all(r.diagnostics == () for r in project.results)
    assert sorted(p.name for p in markers.iterdir()) == [], "analysis executed repository code"
    assert list(root.rglob("__pycache__")) == [], "analysis compiled repository code"
    assert list(root.rglob("*.pyc")) == []
    assert not (set(sys.modules) - before) & set(CANARY_MODULES)

    # Positive control: really importing the package does fire the canaries.
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(root)!r}); import canarypkg.mod",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert {"pkg-init", "pkg-mod"} <= {p.name for p in markers.iterdir()}


# Calls and imports that would load, run or compile code. ``ast.parse`` is the one allowed
# entry point: it only builds a tree (PyCF_ONLY_AST).
_FORBIDDEN_CALLS = frozenset(
    {"exec", "eval", "compile", "__import__", "import_module", "run_path", "run_module", "system",
     "popen", "spec_from_file_location", "exec_module", "load_module", "reload"}
)  # fmt: skip
_FORBIDDEN_IMPORTS = frozenset(
    {"importlib", "runpy", "subprocess", "ctypes", "pickle", "marshal", "multiprocessing",
     "code", "codeop", "pkgutil", "zipimport"}
)  # fmt: skip


@pytest.mark.parametrize("package", ["analysis", "discovery"])
def test_analyzer_source_has_no_execution_primitives(package: str) -> None:
    root = Path(rift.__file__).parent / package
    sources = sorted(root.rglob("*.py"))
    assert sources
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "re":
                    continue  # re.compile builds a regex, not code
                assert name not in _FORBIDDEN_CALLS, f"{path.name}:{node.lineno} calls {name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in _FORBIDDEN_IMPORTS, f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in _FORBIDDEN_IMPORTS, f"{path.name} imports from {node.module}"
