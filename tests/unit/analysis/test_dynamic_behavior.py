"""SPEC §9.4: every dynamic behavior is recorded with the right reason or flag, never guessed.

One fixture project per behavior. ``monkeypatching`` and ``globals()/locals()`` writes have no
dedicated ``UnresolvedReason``; both are recorded as ``dynamic_attribute`` (ADR-0007), with the
source text in ``raw``.
"""

from pathlib import Path

import pytest

from tests.fixtures.python_project import analyze, edge_targets, edges, unresolved

BASE = {"pkg/__init__.py": "", "pkg/target.py": "def f():\n    pass\n"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
            ("dynamic_import", "importlib.import_module(name)"),
            id="import_module-non-literal",
        ),
        pytest.param(
            "from importlib import import_module as im\nmod = im(f'pkg.{1}')\n",
            ("dynamic_import", "im(f'pkg.{1}')"),
            id="import_module-aliased-fstring",
        ),
        pytest.param(
            "def load(name):\n    return __import__(name)\n",
            ("dynamic_import", "__import__(name)"),
            id="__import__-non-literal",
        ),
        pytest.param(
            "import runpy\nrunpy.run_path('script.py')\n",
            ("dynamic_import", "runpy.run_path('script.py')"),
            id="runpy",
        ),
        pytest.param(
            "def read(obj, name):\n    return getattr(obj, name)\n",
            ("dynamic_attribute", "getattr(obj, name)"),
            id="getattr-non-literal",
        ),
        pytest.param(
            "def write(obj, name):\n    setattr(obj, name, 1)\n",
            ("dynamic_attribute", "setattr(obj, name, 1)"),
            id="setattr-non-literal",
        ),
        pytest.param(
            "def drop(obj, name):\n    delattr(obj, name)\n",
            ("dynamic_attribute", "delattr(obj, name)"),
            id="delattr-non-literal",
        ),
        pytest.param("exec('x = 1')\n", ("exec_eval", "exec('x = 1')"), id="exec"),
        pytest.param("y = eval('1 + 1')\n", ("exec_eval", "eval('1 + 1')"), id="eval"),
        pytest.param(
            "code = compile('1', 'f', 'eval')\n",
            ("exec_eval", "compile('1', 'f', 'eval')"),
            id="compile",
        ),
        pytest.param(
            "import builtins\nbuiltins.exec('1')\n",
            ("exec_eval", "builtins.exec('1')"),
            id="exec-qualified",
        ),
        pytest.param(
            "import sys\nsys.path.append('/opt/x')\n",
            ("sys_path_mutation", "sys.path.append('/opt/x')"),
            id="sys.path.append",
        ),
        pytest.param(
            "import sys\nsys.path.insert(0, 'x')\n",
            ("sys_path_mutation", "sys.path.insert(0, 'x')"),
            id="sys.path.insert",
        ),
        pytest.param(
            "import sys\nsys.path = ['x']\n",
            ("sys_path_mutation", "sys.path"),
            id="sys.path-assign",
        ),
        pytest.param(
            "import sys\nsys.path += ['x']\n",
            ("sys_path_mutation", "sys.path"),
            id="sys.path-augassign",
        ),
        pytest.param(
            "import sys\nsys.path[0:0] = ['x']\n",
            ("sys_path_mutation", "sys.path[0:0]"),
            id="sys.path-slice",
        ),
        pytest.param(
            "from sys import path\npath.extend(['x'])\n",
            ("sys_path_mutation", "path.extend(['x'])"),
            id="sys.path-imported",
        ),
        pytest.param(
            "import site\nsite.addsitedir('x')\n",
            ("sys_path_mutation", "site.addsitedir('x')"),
            id="site.addsitedir",
        ),
        pytest.param(
            "def __getattr__(name):\n    return name\n",
            ("module_getattr", "__getattr__"),
            id="module-__getattr__",
        ),
        pytest.param(
            "import pkg.target\npkg.target.f = lambda: 1\n",
            ("dynamic_attribute", "pkg.target.f"),
            id="monkeypatch-project-module",
        ),
        pytest.param(
            "import os\nos.getcwd = lambda: '/'\n",
            ("dynamic_attribute", "os.getcwd"),
            id="monkeypatch-external-module",
        ),
        pytest.param(
            "from pkg import target\nsetattr(target, 'f', None)\n",
            ("dynamic_attribute", "setattr(target, 'f', None)"),
            id="monkeypatch-setattr-literal",
        ),
        pytest.param(
            "globals()['injected'] = 1\n",
            ("dynamic_attribute", "globals()['injected']"),
            id="globals-subscript",
        ),
        pytest.param(
            "globals().update(a=1)\n",
            ("dynamic_attribute", "globals().update(a=1)"),
            id="globals-update",
        ),
        pytest.param(
            "def f():\n    locals()['x'] = 1\n",
            ("dynamic_attribute", "locals()['x']"),
            id="locals-subscript",
        ),
        pytest.param(
            "import sys\nvars(sys.modules[__name__])['x'] = 1\n",
            ("dynamic_attribute", "vars(sys.modules[__name__])['x']"),
            id="vars-subscript",
        ),
    ],
)
def test_dynamic_behavior_is_recorded(
    tmp_path: Path, source: str, expected: tuple[str, str]
) -> None:
    result = analyze(tmp_path, {**BASE, "pkg/m.py": source})["pkg/m.py"]
    found = {u for u in unresolved(result) if u[0] != "external_module"}
    assert found == {expected}


def test_literal_import_module_resolves_likely_with_the_dynamic_flag(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            **BASE,
            "pkg/m.py": """
                import importlib
                from importlib import import_module
                importlib.import_module("pkg.target")
                import_module(".target", package=__package__)
                import_module(".target", "pkg")
                __import__("pkg.target")
                importlib.import_module("pkg.absent")
                importlib.import_module("yaml")
                importlib.import_module(".target", package=compute())
            """,
        },
    )["pkg/m.py"]
    assert ("py:pkg.m", "imports", "py:pkg.target", "likely", frozenset({"dynamic"})) in edges(
        result
    )
    assert unresolved(result) == {
        ("external_module", "importlib"),
        ("missing_module", "pkg.absent"),
        ("external_module", "yaml"),
        ("dynamic_import", 'importlib.import_module(".target", package=compute())'),
        ("unknown", "compute"),
    }


def test_literal_getattr_is_not_dynamic(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"m.py": "def f(o):\n    return getattr(o, 'name', None)\n"})["m.py"]
    assert unresolved(result) == set()


def test_shadowed_builtins_are_not_dynamic(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {"m.py": "def eval(x):\n    return x\ndef g():\n    return eval('1')\n"},
    )["m.py"]
    assert unresolved(result) == set()
    assert ("py:m:g", "py:m:eval", "certain") in edge_targets(result, "calls")


def test_attribute_writes_on_non_modules_are_not_monkeypatching(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            **BASE,
            "pkg/m.py": """
                from pkg.target import f
                class C:
                    def __init__(self):
                        self.value = 1
                f.attribute = 2
                C.shared = 3
            """,
        },
    )["pkg/m.py"]
    assert unresolved(result) == set()


# --------------------------------------------------------------------------- star imports


def test_star_import_from_a_determinable_module(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "lib.py": "__all__ = ['public_fn']\ndef public_fn(): pass\ndef other(): pass\n",
            "m.py": """
                from lib import *
                def use():
                    public_fn()
                    other()
            """,
        },
    )["m.py"]
    assert ("py:m", "imports", "py:lib", "certain", frozenset({"star_import"})) in edges(result)
    assert ("py:m:use", "calls", "py:lib:public_fn", "likely", frozenset({"star_import"})) in edges(
        result
    )
    # ``other`` is not in lib.__all__ and nothing else binds it: truly unknown.
    assert unresolved(result) == {("unknown", "other")}


@pytest.mark.parametrize(
    "lib",
    [
        pytest.param("__all__ = [n for n in dir()]\ndef f(): pass\n", id="non-literal-all"),
        pytest.param("from os import *\n", id="chained-star"),
    ],
)
def test_star_import_whose_names_are_unknowable(tmp_path: Path, lib: str) -> None:
    result = analyze(
        tmp_path, {"lib.py": lib, "m.py": "from lib import *\ndef use():\n    mystery()\n"}
    )["m.py"]
    assert unresolved(result) == {("star_import", "lib.*"), ("star_import", "mystery")}


def test_external_star_import(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"m.py": "from os.path import *\nx = join('a', 'b')\n"})["m.py"]
    assert unresolved(result) == {
        ("external_module", "os.path"),
        ("star_import", "os.path.*"),
        ("star_import", "join"),
    }


def test_names_from_a_module_with_getattr_or_star_are_attributed(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "lazy.py": "def __getattr__(name):\n    return name\n",
            "starry.py": "from os import *\n",
            "plain.py": "x = 1\n",
            "m.py": "from lazy import thing\nfrom starry import other\nfrom plain import nothing\n",
        },
    )["m.py"]
    assert unresolved(result) == {
        ("module_getattr", "lazy.thing"),
        ("star_import", "starry.other"),
        ("missing_module", "plain.nothing"),
    }
