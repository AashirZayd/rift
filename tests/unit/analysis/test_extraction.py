"""SPEC §9.2 extraction and §9.3 resolution on fixture projects (real files in tmp_path)."""

from pathlib import Path

import pytest

from rift.analysis.python.analyzer import analyze_files
from rift.analysis.python.options import AnalysisError
from rift.core.models.analysis import AnalysisResult
from tests.fixtures.python_project import (
    analyze,
    analyze_tree,
    edge_targets,
    edges,
    symbols,
    unresolved,
    write_project,
)

# --------------------------------------------------------------------------- symbols


def test_symbol_kinds_qualnames_and_decorators(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "m.py": """
                import functools
                MAX_SIZE = 3
                _LIMIT: int = 4
                A, B = 1, 2
                lower = 5

                @functools.lru_cache(maxsize=None)
                def top():
                    def inner():
                        pass
                    return inner

                async def fetch():
                    pass

                class Outer:
                    CONST = 1
                    class Inner:
                        def method(self):
                            pass
                    @property
                    def value(self):
                        return 1
                    @value.setter
                    def value(self, v):
                        pass
                    @staticmethod
                    def make():
                        pass
            """
        },
    )["m.py"]
    assert symbols(result) == {
        "m": ("module", "public"),
        "MAX_SIZE": ("constant", "public"),
        "_LIMIT": ("constant", "private"),
        "A": ("constant", "public"),
        "B": ("constant", "public"),
        "top": ("function", "public"),
        "top.<locals>.inner": ("function", "public"),
        "fetch": ("function", "public"),
        "Outer": ("class", "public"),
        "Outer.Inner": ("class", "public"),
        "Outer.Inner.method": ("method", "public"),
        "Outer.value": ("method", "public"),
        "Outer.make": ("method", "public"),
    }
    by_name = {s.qualname: s for s in result.symbols}
    assert by_name["top"].decorators == ("functools.lru_cache",)
    assert by_name["Outer.value"].decorators == ("property", "value.setter")
    # The span includes decorators, and a redefinition (setter) widens it over both defs.
    assert by_name["top"].location.start_line == 7
    assert (
        by_name["Outer.value"].location.start_line,
        by_name["Outer.value"].location.end_line,
    ) == (
        21,
        26,
    )
    module = by_name["m"]
    assert (module.location.start_line, module.location.end_line) == (1, 29)


def test_test_symbols_follow_pytest_default_naming(tmp_path: Path) -> None:
    results = analyze(
        tmp_path,
        {
            "tests/test_things.py": """
                def test_one():
                    pass
                def helper():
                    pass
                class TestGroup:
                    def test_two(self):
                        pass
                    def setup_method(self):
                        pass
                class Helper:
                    def test_not_collected(self):
                        pass
            """,
            "tests/things_test.py": "def test_three():\n    pass\n",
            "src_test_helper.py": "def test_not_a_test_file():\n    pass\n",
        },
    )
    assert symbols(results["tests/test_things.py"]) == {
        "tests.test_things": ("module", "public"),
        "test_one": ("test_function", "public"),
        "helper": ("function", "public"),
        "TestGroup": ("class", "public"),
        "TestGroup.test_two": ("test_method", "public"),
        "TestGroup.setup_method": ("method", "public"),
        "Helper": ("class", "public"),
        "Helper.test_not_collected": ("method", "public"),
    }
    assert symbols(results["tests/things_test.py"])["test_three"] == ("test_function", "public")
    assert symbols(results["src_test_helper.py"])["test_not_a_test_file"][0] == "function"


def test_literal_all_decides_top_level_visibility(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "m.py": """
                __all__ = ["exported", "_also"]
                __all__ += ["added"]
                __all__.append("appended")
                __all__.extend(("extended",))
                def exported(): pass
                def _also(): pass
                def added(): pass
                def appended(): pass
                def extended(): pass
                def hidden(): pass
                def __dunder__(): pass
                class Public:
                    def method(self): pass
            """
        },
    )["m.py"]
    visibility = {q: v for q, (_, v) in symbols(result).items()}
    assert visibility["exported"] == visibility["_also"] == "public"
    assert visibility["added"] == visibility["appended"] == visibility["extended"] == "public"
    assert visibility["hidden"] == visibility["Public"] == "private"  # __all__ wins
    assert visibility["__dunder__"] == "dunder"
    assert visibility["Public.method"] == "public"  # __all__ only governs module names
    assert result.limitations == ()


@pytest.mark.parametrize(
    "assignment",
    [
        "__all__ = [name for name in dir()]",
        "__all__ = ['a'] + other.__all__",
        "__all__.remove('a')",
        "__all__ += make()",
        "__all__, x = ['a'], 1",
    ],
)
def test_non_literal_all_is_a_limitation(tmp_path: Path, assignment: str) -> None:
    result = analyze(tmp_path, {"m.py": f"import other\n{assignment}\ndef a(): pass\n"})["m.py"]
    assert [(lim.kind, lim.subject) for lim in result.limitations] == [("non_literal_all", "m.py")]
    assert symbols(result)["a"] == ("function", "public")  # falls back to the underscore rule


def test_unrepresentable_symbol_names_are_limitations(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"m.py": "def café():\n    pass\n"})["m.py"]
    assert [lim.kind for lim in result.limitations] == ["unrepresentable_symbol"]
    assert set(symbols(result)) == {"m"}


def test_files_without_an_importable_name_have_no_symbols(tmp_path: Path) -> None:
    results = analyze(
        tmp_path,
        {
            "my-scripts/run.py": "import pkg.a\ndef main(): pass\n",
            "pkg/__init__.py": "",
            "pkg/a.py": "",
        },
    )
    script = results["my-scripts/run.py"]
    assert script.module is None
    assert script.symbols == ()
    assert [lim.kind for lim in script.limitations] == ["unnamed_module"]
    # Its edges start at its file node.
    assert ("file:my-scripts/run.py", "py:pkg.a", "certain") in edge_targets(script, "imports")


# --------------------------------------------------------------------------- module naming


def test_src_layout_is_autodetected_and_tests_use_the_repo_root(tmp_path: Path) -> None:
    project = analyze_tree(
        write_project(
            tmp_path,
            {
                "src/app/__init__.py": "",
                "src/app/core.py": "def run(): pass\n",
                "tests/test_core.py": "from app.core import run\ndef test_run():\n    run()\n",
                "setup.py": "",
            },
        )
    )
    assert project.source_roots == ("src",)
    by_path = {r.file_path: r for r in project.results}
    assert by_path["src/app/core.py"].module == "app.core"
    assert by_path["tests/test_core.py"].module == "tests.test_core"
    assert by_path["setup.py"].module == "setup"
    assert ("py:tests.test_core:test_run", "py:app.core:run", "certain") in edge_targets(
        by_path["tests/test_core.py"], "calls"
    )


def test_repo_root_is_the_source_root_without_src_packages(tmp_path: Path) -> None:
    project = analyze_tree(
        write_project(tmp_path, {"app/__init__.py": "", "src/not-a-package/x.py": ""})
    )
    assert project.source_roots == (".",)


def test_configured_source_roots_override_autodetection(tmp_path: Path) -> None:
    project = analyze_tree(
        write_project(tmp_path, {"lib/pkg/mod.py": "", "src/other/mod.py": ""}),
        source_roots=("lib",),
    )
    assert project.source_roots == ("lib",)
    names = {r.file_path: r.module for r in project.results}
    assert names == {"lib/pkg/mod.py": "pkg.mod", "src/other/mod.py": "src.other.mod"}


def test_package_beats_same_named_module_and_the_loser_is_recorded(tmp_path: Path) -> None:
    results = analyze(tmp_path, {"thing.py": "", "thing/__init__.py": ""})
    assert results["thing/__init__.py"].module == "thing"
    loser = results["thing.py"]
    assert loser.module is None
    assert [(lim.kind, lim.message) for lim in loser.limitations] == [
        ("module_shadowed", "module name 'thing' belongs to 'thing/__init__.py'")
    ]


def test_namespace_packages_resolve(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "ns/sub/mod.py": "def f(): pass\n",  # neither ns/ nor ns/sub/ has __init__.py
            "user.py": """
                import ns.sub.mod
                import ns
                from ns.sub import mod as alias
                from ns.sub.mod import f
                def go():
                    f()
                    ns.sub.mod.f()
                    alias.f()
            """,
        },
    )["user.py"]
    assert edge_targets(result, "imports") == {("py:user", "py:ns.sub.mod", "certain")}
    assert edge_targets(result, "calls") == {("py:user:go", "py:ns.sub.mod:f", "certain")}
    assert unresolved(result) == set()


# --------------------------------------------------------------------------- imports


def test_import_forms_and_ancestor_packages(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/leaf.py": "X = 1\n",
            "pkg/a.py": """
                from __future__ import annotations
                import os, os.path
                import pkg.sub.leaf
                import pkg.sub.leaf as leaf_alias
                from pkg.sub import leaf
                from . import b
                from .sub.leaf import X
                from .. import outside
                import pkg.nope
                from pkg.sub import missing_name
                def use():
                    return X
            """,
            "pkg/b.py": "",
        },
    )["pkg/a.py"]
    assert edge_targets(result, "imports") == {
        ("py:pkg.a", "py:pkg", "certain"),
        ("py:pkg.a", "py:pkg.sub", "certain"),
        ("py:pkg.a", "py:pkg.sub.leaf", "certain"),
        ("py:pkg.a", "py:pkg.b", "certain"),
    }
    assert unresolved(result) == {
        ("external_module", "os"),
        ("external_module", "os.path"),
        ("missing_module", ".."),
        ("missing_module", "pkg.nope"),
        ("missing_module", "pkg.sub.missing_name"),
    }
    assert [d.code for d in result.diagnostics] == ["relative_import_beyond_top_level"]
    assert ("py:pkg.a:use", "py:pkg.sub.leaf:X", "certain") in edge_targets(result, "references")


def test_relative_import_beyond_top_level_from_a_top_level_module(tmp_path: Path) -> None:
    result = analyze(tmp_path, {"top.py": "from . import sibling\n", "sibling.py": ""})["top.py"]
    assert unresolved(result) == {("missing_module", ".")}
    assert [d.code for d in result.diagnostics] == ["relative_import_beyond_top_level"]


def test_type_checking_imports_are_flagged(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/types.py": "",
            "pkg/runtime.py": "",
            "pkg/other.py": "",
            "pkg/m.py": """
                import typing
                from typing import TYPE_CHECKING
                if TYPE_CHECKING:
                    from pkg import types
                else:
                    from pkg import runtime
                if typing.TYPE_CHECKING:
                    import pkg.other
            """,
        },
    )["pkg/m.py"]
    flagged = {(e[2], e[4]) for e in edges(result) if e[1] == "imports"}
    assert flagged == {
        # ``from pkg import x`` runs pkg/__init__.py first: a real dependency on the package.
        ("py:pkg", frozenset({"type_checking_only"})),
        ("py:pkg", frozenset({"conditional"})),
        ("py:pkg.types", frozenset({"type_checking_only"})),
        ("py:pkg.runtime", frozenset({"conditional"})),
        ("py:pkg.other", frozenset({"type_checking_only"})),
    }


def test_try_except_import_error_imports_are_conditional(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "fast.py": "",
            "slow.py": "",
            "plain.py": "",
            "always.py": "",
            "m.py": """
                try:
                    import fast as impl
                except ImportError:
                    import slow as impl
                finally:
                    import always
                try:
                    import plain
                except ValueError:
                    pass
                try:
                    import fast
                except (OSError, ModuleNotFoundError):
                    pass
            """,
        },
    )["m.py"]
    flagged = {(e[2], e[4]) for e in edges(result) if e[1] == "imports"}
    assert flagged == {
        ("py:fast", frozenset({"conditional"})),
        ("py:slow", frozenset({"conditional"})),
        ("py:always", frozenset()),
        ("py:plain", frozenset()),
    }


# --------------------------------------------------------------------------- calls


def test_call_confidence_tiers(tmp_path: Path) -> None:
    results = analyze(
        tmp_path,
        {
            "pkg/__init__.py": "from pkg.impl import helper as helper\n",
            "pkg/impl.py": """
                def helper():
                    pass
                class Base:
                    def shared(self):
                        pass
                    def hook(self):
                        pass
            """,
            "pkg/user.py": """
                from pkg import helper
                import pkg.impl
                from pkg.impl import Base

                def local():
                    pass

                class Child(Base):
                    def own(self):
                        pass
                    def hook(self):
                        super().hook()
                        self.own()
                        self.shared()
                        local()
                        helper()
                        pkg.impl.helper()
                        Child.own(self)
                        Child().own()

                def unknown_receiver(thing):
                    thing.shared()
                    thing.not_a_project_method()
                    make().hook()
            """,
        },
    )
    calls = edge_targets(results["pkg/user.py"], "calls")
    hook = "py:pkg.user:Child.hook"
    assert calls >= {
        (hook, "py:pkg.impl:Base.hook", "likely"),  # super()
        (hook, "py:pkg.user:Child.own", "likely"),  # self.own(), Child().own()
        (hook, "py:pkg.impl:Base.shared", "likely"),  # inherited
        (hook, "py:pkg.user:local", "certain"),
        (hook, "py:pkg.impl:helper", "certain"),  # through the pkg re-export
        (hook, "py:pkg.user:Child.own", "certain"),  # Child.own(self)
    }
    receiver = "py:pkg.user:unknown_receiver"
    assert {c for c in calls if c[0] == receiver} == {
        (receiver, "py:pkg.impl:Base.shared", "possible"),
        (receiver, "py:pkg.impl:Base.hook", "possible"),
        (receiver, "py:pkg.user:Child.hook", "possible"),
    }
    assert ("py:pkg.user:Child", "py:pkg.impl:Base", "certain") in edge_targets(
        results["pkg/user.py"], "inherits"
    )
    assert unresolved(results["pkg/user.py"]) == {("unknown", "make")}


def test_calls_to_external_and_builtin_names_are_not_guessed(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "lib.py": "class Thing:\n    def get(self): pass\n    def join(self): pass\n",
            "m.py": """
                import requests
                from os import path
                def f(items):
                    requests.get("x")
                    path.join("a", "b")
                    len(items)
                    str.join(",", items)
            """,
        },
    )["m.py"]
    assert edge_targets(result, "calls") == set()
    assert unresolved(result) == {("external_module", "requests"), ("external_module", "os")}


def test_conditional_redefinitions_are_likely(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "m.py": """
                try:
                    from fastlib import parse
                except ImportError:
                    def parse(text):
                        return text
                def use():
                    parse("x")
            """
        },
    )["m.py"]
    assert ("py:m:use", "py:m:parse", "likely") in edge_targets(result, "calls")


def test_decorators_and_references_produce_reference_edges(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "m.py": """
                LIMIT = 3
                def register(fn):
                    return fn
                @register
                def handler():
                    return LIMIT
                CALLBACKS = [handler]
            """
        },
    )["m.py"]
    assert edge_targets(result, "references") == {
        ("py:m:handler", "py:m:register", "certain"),
        ("py:m:handler", "py:m:LIMIT", "certain"),
        ("py:m", "py:m:handler", "certain"),
    }


def test_scoping_rules(tmp_path: Path) -> None:
    result = analyze(
        tmp_path,
        {
            "m.py": """
                def target():
                    pass
                def shadowed(target):
                    target()
                class C:
                    target = 1
                    def method(self):
                        target()
                def uses_global():
                    global late
                    late = 1
                def reads_late():
                    return late
                squares = [x * x for x in range(3)]
                fn = lambda y: y
            """
        },
    )["m.py"]
    calls = edge_targets(result, "calls")
    assert ("py:m:shadowed", "py:m:target", "certain") not in calls  # parameter shadows
    assert ("py:m:C.method", "py:m:target", "certain") in calls  # class scope is skipped
    assert unresolved(result) == set()  # late (global), x, y are all bound


# --------------------------------------------------------------------------- inputs


def test_analyze_files_rejects_bad_paths(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError):
        analyze_files(tmp_path, ["../escape.py"])
    with pytest.raises(AnalysisError):
        analyze_files(tmp_path, ["notes.txt"])
    with pytest.raises(AnalysisError):
        analyze_files(Path("relative"), [])


def test_results_are_sorted_and_one_per_file(tmp_path: Path) -> None:
    project = analyze_tree(write_project(tmp_path, {"b.py": "", "a.py": "", "c/d.py": ""}))
    assert [r.file_path for r in project.results] == ["a.py", "b.py", "c/d.py"]
    assert all(isinstance(r, AnalysisResult) for r in project.results)
