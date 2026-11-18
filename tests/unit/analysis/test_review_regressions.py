"""Regression tests for the M2 fresh-context review findings (one test per finding)."""

import builtins
import importlib
import os
import sys
import time
from pathlib import Path

import pytest

from rift.analysis.python.analyzer import analyze_files
from rift.analysis.python.options import AnalysisError
from rift.analysis.python.syntax import newer_syntax
from tests.fixtures.python_project import (
    analyze,
    analyze_tree,
    edge_targets,
    edges,
    unresolved,
    write_project,
)

# --------------------------------------------------------------------------- 1. star cycles


def test_self_star_import_does_not_crash(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"a.py": "from a import *\nfoo()\n"})["a.py"]
    assert result.diagnostics == ()
    assert ("unknown", "foo") in unresolved(result) or ("star_import", "foo") in unresolved(result)


def test_mutual_star_imports_do_not_crash(tmp_path: Path) -> None:
    results = analyze(
        tmp_path,
        {
            "a.py": "from b import *\n",
            "b.py": "from a import *\n",
            "c.py": "from a import foo\nimport a\ndef use():\n    a.foo()\n    foo()\n",
        },
    )
    assert all(r.diagnostics == () for r in results.values())
    reasons = {reason for reason, raw in unresolved(results["c.py"]) if raw == "a.foo"}
    assert reasons and reasons <= {"unknown", "star_import"}


# --------------------------------------------------------------------------- 2. work bound


def test_star_and_reexport_fan_out_is_not_exponential(tmp_path: Path) -> None:
    n = 60  # exponential resolution would need ~1.6**60 steps
    files: dict[str, str | bytes] = {
        f"s{i}.py": f"from s{i + 1} import *\nfrom s{i + 2} import *\n" for i in range(n)
    }
    files.update(
        {f"r{i}.py": "".join(f"from r{i + k} import x\n" for k in (1, 2, 3)) for i in range(n)}
    )
    files[f"s{n}.py"] = files[f"s{n + 1}.py"] = "def foo(): pass\n"
    files.update({f"r{n + k}.py": "def x(): pass\n" for k in (0, 1, 2)})
    files["use.py"] = "from s0 import *\nfrom r0 import x\ndef go():\n    foo()\n    x()\n"
    started = time.monotonic()
    result = analyze(tmp_path, files)["use.py"]
    assert time.monotonic() - started < 60  # linear: well under a second in practice
    calls = edge_targets(result, "calls")
    assert ("py:use:go", f"py:s{n}:foo", "likely") in calls


# --------------------------------------------------------------------------- 3. containment


def test_backslash_paths_are_rejected_on_windows(tmp_path: Path) -> None:
    root = write_project(tmp_path / "repo", {"ok.py": ""})
    write_project(tmp_path / "outside", {"secret.py": "TOKEN = 1\n"})
    if os.name == "nt":
        with pytest.raises(AnalysisError):
            analyze_files(root, ["..\\outside\\secret.py"])
    else:  # a backslash is an ordinary filename character: no such file inside the root
        project = analyze_files(root, ["..\\outside\\secret.py"])
        assert [d.code for d in project.results[0].diagnostics] == ["unreadable_file"]


def _dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        create_junction = vars(importlib.import_module("_winapi"))["CreateJunction"]
        create_junction(str(target), str(link))
        return
    link.symlink_to(target, target_is_directory=True)


def test_files_behind_directory_links_are_never_read(tmp_path: Path) -> None:
    root = write_project(tmp_path / "repo", {"ok.py": ""})
    outside = write_project(tmp_path / "outside", {"secret.py": "SECRET_TOKEN = 1\n"})
    _dir_link(root / "linked", outside)
    project = analyze_files(root, ["linked/secret.py", "ok.py"])
    linked = project.results[0]
    assert [d.code for d in linked.diagnostics] == ["not_a_regular_file"]
    assert all("SECRET" not in s.qualname for s in linked.symbols)


# --------------------------------------------------------------------------- 4. src package


def test_src_that_is_a_package_is_not_a_source_root(tmp_path: Path) -> None:
    project = analyze_tree(
        write_project(
            tmp_path,
            {
                "src/__init__.py": "",
                "src/a.py": "def f(): pass\n",
                "b.py": "from src.a import f\nf()\n",
            },
        )
    )
    assert project.source_roots == (".",)
    by_path = {r.file_path: r for r in project.results}
    assert by_path["src/a.py"].module == "src.a"
    assert unresolved(by_path["b.py"]) == set()
    assert ("py:b", "py:src.a:f", "certain") in edge_targets(by_path["b.py"], "calls")


# --------------------------------------------------------------------------- 5. pytest sys.path


def test_pytest_style_sibling_imports_resolve_likely(tmp_path: Path) -> None:
    results = analyze(
        tmp_path,
        {
            "src/app/__init__.py": "",
            "tests/helpers.py": "def mk(): pass\n",
            "tests/test_a.py": (
                "from helpers import mk\nimport helpers\n"
                "def test_x():\n    mk()\n    helpers.mk()\n"
            ),
        },
    )
    result = results["tests/test_a.py"]
    assert unresolved(result) == set()
    assert ("py:tests.test_a", "py:tests.helpers", "likely") in edge_targets(result, "imports")
    assert ("py:tests.test_a:test_x", "py:tests.helpers:mk", "likely") in edge_targets(
        result, "calls"
    )


# --------------------------------------------------------------------------- 6. rebinding


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def g():\n    from pkg.t import f\n    f = lambda: 1\n    f()\n", id="local-rebind"
        ),
        pytest.param(
            "from pkg.t import f\ndef reset():\n    global f\n    f = None\ndef g():\n    f()\n",
            id="global-rebind",
        ),
        pytest.param(
            "from pkg.t import f\ntry:\n    pass\nexcept Exception as f:\n    pass\n"
            "def g():\n    f()\n",
            id="except-rebind",
        ),
        pytest.param(
            "from pkg.t import f\nmatch 1:\n    case f:\n        pass\ndef g():\n    f()\n",
            id="match-capture",
        ),
    ],
)
def test_rebound_names_are_not_certain(tmp_path: Path, source: str) -> None:
    result = analyze(
        tmp_path, {"pkg/__init__.py": "", "pkg/t.py": "def f(): pass\n", "pkg/m.py": source}
    )["pkg/m.py"]
    confidences = {c for (_, target, c) in edge_targets(result, "calls") if target == "py:pkg.t:f"}
    assert confidences == {"likely"}


# --------------------------------------------------------------------------- 7. re-exported APIs


def test_dynamic_apis_reexported_through_project_modules_are_recorded(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "compat.py": "from importlib import import_module\nfrom builtins import eval as ev\n",
            "m.py": (
                "from compat import import_module, ev\n"
                "def f(n):\n    import_module(n)\n    ev('1')\n"
            ),
        },
    )["m.py"]
    assert unresolved(result) == {
        ("dynamic_import", "import_module(n)"),
        ("exec_eval", "ev('1')"),
    }


# --------------------------------------------------------------------------- 8. deep / cyclic


def test_deep_reexport_chains_and_cycles_are_unknown_not_missing(tmp_path: Path) -> None:
    n = 80  # deeper than the resolution cap
    files: dict[str, str | bytes] = {f"m{i}.py": f"from m{i + 1} import x\n" for i in range(n)}
    files[f"m{n}.py"] = "def x(): pass\n"
    files["a.py"] = "from b import y\n"
    files["b.py"] = "from a import y\n"
    files["use.py"] = "from m0 import x\nfrom a import y\n"
    results = analyze(tmp_path, files)
    assert all(r.diagnostics == () for r in results.values())
    found = unresolved(results["use.py"])
    assert ("missing_module", "m0.x") not in found
    assert ("missing_module", "a.y") not in found
    assert ("unknown", "a.y") in found


# --------------------------------------------------------------------------- 9. all candidates


def test_attribute_chains_follow_every_candidate(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "pkg/__init__.py": (
                "try:\n    from pkg.fast import helper\n"
                "except ImportError:\n    from pkg.slow import helper\n"
            ),
            "pkg/fast.py": "def helper(): pass\n",
            "pkg/slow.py": "def helper(): pass\n",
            "m.py": "import pkg\ndef go():\n    pkg.helper()\n",
        },
    )["m.py"]
    assert {t for (_, t, _c) in edge_targets(result, "calls")} == {
        "py:pkg.fast:helper",
        "py:pkg.slow:helper",
    }


# --------------------------------------------------------------------------- 10. builtins


def test_builtins_are_a_static_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtins, "injected_at_runtime", 1, raising=False)
    result = analyze(tmp_path, {"m.py": "help(len)\nexit(0)\ninjected_at_runtime()\n"})["m.py"]
    assert unresolved(result) == {("unknown", "injected_at_runtime")}


# --------------------------------------------------------------------------- 11. t-strings


def test_escapes_are_not_template_strings() -> None:
    assert newer_syntax('x = "a\\t" +', (3, 13)) is None
    assert newer_syntax("x = t'a'", (3, 13)) is not None


# --------------------------------------------------------------------------- 12. smaller items


def test_relative_import_in_an_unnamed_file_has_its_own_code(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"my-dir/m.py": "from . import x\n"})["my-dir/m.py"]
    assert [d.code for d in result.diagnostics] == ["relative_import_without_package"]


def test_loop_and_match_imports_are_conditional_and_aliased_type_checking(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "a.py": "",
            "b.py": "",
            "c.py": "",
            "m.py": """
                from typing import TYPE_CHECKING as TC
                if TC:
                    import a
                for _ in range(1):
                    import b
                match 1:
                    case 1:
                        import c
            """,
        },
    )["m.py"]
    flagged = {(e[2], e[4]) for e in edges(result) if e[1] == "imports"}
    assert flagged == {
        ("py:a", frozenset({"type_checking_only"})),
        ("py:b", frozenset({"conditional"})),
        ("py:c", frozenset({"conditional"})),
    }


def test_decorator_span_starts_at_the_at_sign(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"m.py": "@ (lambda f: f)\ndef g():\n    pass\n"})["m.py"]
    (symbol,) = [s for s in result.symbols if s.qualname == "g"]
    assert (symbol.location.start_line, symbol.location.start_col) == (1, 0)


def test_dunder_import_level_and_sys_modules_injection(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sib.py": "",
            "pkg/m.py": """
                import sys
                __import__("sib", globals(), locals(), [], 1)
                __import__("sib", level=lvl())
                sys.modules["fake"] = object()
                sys.modules.update({})
            """,
        },
    )["pkg/m.py"]
    assert ("py:pkg.m", "imports", "py:pkg.sib", "likely", frozenset({"dynamic"})) in edges(result)
    assert {u for u in unresolved(result) if u[0] == "dynamic_import"} == {
        ("dynamic_import", '__import__("sib", level=lvl())'),
        ("dynamic_import", 'sys.modules["fake"]'),
        ("dynamic_import", "sys.modules.update({})"),
    }


@pytest.mark.skipif(sys.platform == "win32", reason="covered by the Windows junction variant")
def test_posix_symlinked_directories_are_never_read(tmp_path: Path) -> None:
    root = write_project(tmp_path / "repo", {"ok.py": ""})
    outside = write_project(tmp_path / "outside", {"secret.py": "SECRET = 1\n"})
    (root / "linked").symlink_to(outside, target_is_directory=True)
    project = analyze_files(root, ["linked/secret.py"])
    assert [d.code for d in project.results[0].diagnostics] == ["not_a_regular_file"]
