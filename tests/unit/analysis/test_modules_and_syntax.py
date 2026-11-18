"""Module index naming rules and the newer-syntax table (pure unit tests, no files)."""

import pytest

from rift.analysis.python.modules import ModuleIndex
from rift.analysis.python.options import AnalysisError, AnalysisOptions
from rift.analysis.python.syntax import newer_syntax


def test_names_relative_to_source_roots_with_repo_root_fallback() -> None:
    index = ModuleIndex(
        ["src/pkg/__init__.py", "src/pkg/a.py", "tests/test_a.py", "conftest.py"], ["src"]
    )
    assert index.module_of("src/pkg/__init__.py") == "pkg"
    assert index.module_of("src/pkg/a.py") == "pkg.a"
    assert index.module_of("tests/test_a.py") == "tests.test_a"
    assert index.module_of("conftest.py") == "conftest"
    assert index.is_namespace("tests")
    assert not index.is_namespace("pkg")
    assert index.regular_ancestors("pkg.a") == ("pkg",)


def test_configured_root_beats_fallback_and_package_beats_module() -> None:
    index = ModuleIndex(["lib/x.py", "x.py", "lib/y/__init__.py", "lib/y.py"], ["lib"])
    assert index.module_of("lib/x.py") == "x"
    assert index.module_of("x.py") is None
    assert index.module_of("lib/y/__init__.py") == "y"
    assert index.module_of("lib/y.py") is None
    assert [(s.path, s.winner) for s in index.shadowed] == [
        ("lib/y.py", "lib/y/__init__.py"),
        ("x.py", "lib/x.py"),
    ]


def test_nested_source_roots_prefer_the_most_specific() -> None:
    index = ModuleIndex(["a/b/m.py", "a/n.py"], ["a", "a/b"])
    assert index.module_of("a/b/m.py") == "m"
    assert index.module_of("a/n.py") == "n"


@pytest.mark.parametrize("path", ["my-pkg/m.py", "pkg/1st.py", "café.py", "__init__.py"])
def test_paths_without_an_importable_name(path: str) -> None:
    assert ModuleIndex([path], ["."]).module_of(path) is None


def test_package_of_and_internal() -> None:
    index = ModuleIndex(["pkg/__init__.py", "pkg/a.py", "top.py"], ["."])
    assert index.package_of("pkg") == "pkg"
    assert index.package_of("pkg.a") == "pkg"
    assert index.package_of("top") == ""
    assert index.is_internal("pkg.missing")
    assert not index.is_internal("os.path")


@pytest.mark.parametrize(
    ("line", "running", "since"),
    [
        ("type Alias = int", (3, 11), (3, 12)),
        ("type Pair[T] = tuple[T, T]", (3, 11), (3, 12)),
        ("def first[T](xs: list[T]) -> T:", (3, 11), (3, 12)),
        ("class Box[T]:", (3, 11), (3, 12)),
        ("except ValueError, TypeError:", (3, 13), (3, 14)),
        ("greeting = t'hello {name}'", (3, 13), (3, 14)),
    ],
)
def test_newer_syntax_is_recognised(
    line: str, running: tuple[int, int], since: tuple[int, int]
) -> None:
    found = newer_syntax(line, running)
    assert found is not None
    assert found.since == since


@pytest.mark.parametrize(
    ("line", "running"),
    [
        ("type Alias = int", (3, 12)),  # supported by the running interpreter
        ("except (ValueError, TypeError):", (3, 11)),
        ("def f(:", (3, 11)),
        ("x = 'it'", (3, 11)),
        ("print(type(x))", (3, 11)),
    ],
)
def test_other_lines_are_plain_syntax_errors(line: str, running: tuple[int, int]) -> None:
    assert newer_syntax(line, running) is None


@pytest.mark.parametrize(
    "kwargs",
    [{"max_file_bytes": -1}, {"max_file_bytes": True}, {"source_roots": ()}],
)
def test_invalid_options(kwargs: dict[str, object]) -> None:
    with pytest.raises(AnalysisError):
        AnalysisOptions(**kwargs)  # type: ignore[arg-type]
