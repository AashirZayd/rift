"""ADR-0010 §9: ``--symbol`` resolution. An exact full name or qualname wins; an ambiguous name
returns every candidate, sorted; nothing found is a structured error."""

from pathlib import Path

import pytest

from rift.core.errors import ErrorCode
from rift.graph.builder import build_graph
from rift.impact.errors import ImpactError, SymbolNotFoundError
from rift.impact.index import ProjectIndex
from rift.impact.lookup import SymbolLookup, find_symbol
from tests.unit.impact.helpers import analyze

FILES: dict[str, str | bytes] = {
    "pkg/__init__.py": "",
    "pkg/auth.py": (
        "class Session:\n    def refresh(self):\n        pass\ndef refresh():\n    pass\n"
    ),
    "pkg/cache.py": "class Store:\n    def refresh(self):\n        pass\n",
    "tests/test_auth.py": "def test_refresh():\n    pass\n",
}


@pytest.fixture(scope="module")
def lookup(tmp_path_factory: pytest.TempPathFactory) -> object:
    analysis = analyze(tmp_path_factory.mktemp("lookup"), FILES)
    graph = build_graph(analysis.results).graph
    index = ProjectIndex(analysis)
    return lambda query: find_symbol(query, graph=graph, index=index)


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param(
            "pkg.auth.Session.refresh", "py:pkg.auth:Session.refresh", id="dotted-full-name"
        ),
        pytest.param(
            "pkg.auth:Session.refresh", "py:pkg.auth:Session.refresh", id="module-colon-qualname"
        ),
        pytest.param("py:pkg.cache:Store.refresh", "py:pkg.cache:Store.refresh", id="node-id"),
        pytest.param("pkg.auth", "py:pkg.auth", id="module"),
        pytest.param("Session.refresh", "py:pkg.auth:Session.refresh", id="unique-qualname"),
        pytest.param("refresh", "py:pkg.auth:refresh", id="exact-qualname-beats-short-names"),
        pytest.param("Store", "py:pkg.cache:Store", id="unique-short-name"),
        pytest.param(
            "tests/test_auth.py::test_refresh",
            "test:tests/test_auth.py::test_refresh",
            id="pytest-id",
        ),
        pytest.param("  Store  ", "py:pkg.cache:Store", id="surrounding-whitespace"),
    ],
)
def test_unambiguous_queries_match(lookup: object, query: str, match: str) -> None:
    result: SymbolLookup = lookup(query)  # type: ignore[operator]  # fixture returns a callable
    assert (result.match, result.candidates, result.is_ambiguous) == (match, (match,), False)


def test_ambiguous_name_returns_every_candidate_sorted(tmp_path: Path) -> None:
    files = {**FILES, "pkg/auth.py": "class Session:\n    def refresh(self):\n        pass\n"}
    analysis = analyze(tmp_path, files)
    result = find_symbol(
        "refresh", graph=build_graph(analysis.results).graph, index=ProjectIndex(analysis)
    )
    assert result.is_ambiguous
    assert result.match is None
    assert result.candidates == ("py:pkg.auth:Session.refresh", "py:pkg.cache:Store.refresh")


def test_nothing_found_is_a_structured_error(lookup: object) -> None:
    with pytest.raises(SymbolNotFoundError) as exc_info:
        lookup("nonexistent")  # type: ignore[operator]  # fixture returns a callable
    assert exc_info.value.code is ErrorCode.SYMBOL_NOT_FOUND
    assert exc_info.value.hint


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_is_rejected(lookup: object, query: str) -> None:
    with pytest.raises(ImpactError) as exc_info:
        lookup(query)  # type: ignore[operator]  # fixture returns a callable
    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
