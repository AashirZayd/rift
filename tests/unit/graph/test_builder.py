"""ADR-0009: building the graph from real M2 analysis results.

Covers node kinds and IDs, test nodes, constant folding, edge attributes kept, missing targets
turned into unresolved references (never dropped), carried-over unresolved references, and
independence from input order.
"""

import dataclasses
from pathlib import Path

import pytest

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.analysis import AnalysisResult, edge_sort_key, sort_unique
from rift.core.models.graph import (
    EdgeFlag,
    EdgeKind,
    NodeKind,
    UnresolvedReason,
)
from rift.graph.builder import GraphBuild, build_graph
from rift.graph.errors import GraphError
from tests.fixtures.python_project import analyze

PROJECT = {
    "pkg/__init__.py": "",
    "pkg/c.py": "LIMIT = 3\n",
    "pkg/m.py": """
        from typing import TYPE_CHECKING
        from pkg.c import LIMIT
        if TYPE_CHECKING:
            from pkg import c
        def f():
            return LIMIT
        class Engine:
            def go(self):
                return f()
        def g(obj):
            obj.go()
    """,
    "pkg/a.py": "x = 1\n",  # shadowed by the package below: a file without a module
    "pkg/a/__init__.py": "",
    "tests/test_x.py": """
        from pkg.m import f
        class TestA:
            def test_b(self):
                f()
        def test_c(cb):
            TestA().test_b()
            cb()
        def helper():
            class TestLocal:
                def test_z(self):
                    pass
    """,
}


def n(value: str) -> NodeId:
    return NodeId(value)


@pytest.fixture
def results(tmp_path: Path) -> dict[str, AnalysisResult]:
    return analyze(tmp_path, PROJECT)


@pytest.fixture
def built(results: dict[str, AnalysisResult]) -> GraphBuild:
    return build_graph(results.values())


def _edge_set(build: GraphBuild) -> set[tuple[str, str, str, str, str, frozenset[str]]]:
    return {
        (e.source, e.kind.value, e.target, e.origin.value, e.confidence.value, frozenset(e.flags))
        for e in build.graph.edges()
    }


def test_every_file_and_symbol_becomes_a_node_of_the_right_kind(built: GraphBuild) -> None:
    kinds = {node.id: node.kind for node in built.graph.nodes()}
    assert kinds == {
        "file:pkg/__init__.py": NodeKind.FILE,
        "file:pkg/a.py": NodeKind.FILE,
        "file:pkg/a/__init__.py": NodeKind.FILE,
        "file:pkg/c.py": NodeKind.FILE,
        "file:pkg/m.py": NodeKind.FILE,
        "file:tests/test_x.py": NodeKind.FILE,
        "py:pkg": NodeKind.MODULE,
        "py:pkg.a": NodeKind.MODULE,
        "py:pkg.c": NodeKind.MODULE,
        "py:pkg.m": NodeKind.MODULE,
        "py:pkg.m:Engine": NodeKind.CLASS,
        "py:pkg.m:Engine.go": NodeKind.METHOD,
        "py:pkg.m:f": NodeKind.FUNCTION,
        "py:pkg.m:g": NodeKind.FUNCTION,
        "py:tests.test_x": NodeKind.MODULE,
        "py:tests.test_x:TestA": NodeKind.CLASS,
        "py:tests.test_x:helper": NodeKind.FUNCTION,
        "py:tests.test_x:helper.<locals>.TestLocal": NodeKind.CLASS,
        # pytest cannot collect a test class defined inside a function
        "py:tests.test_x:helper.<locals>.TestLocal.test_z": NodeKind.METHOD,
        "test:tests/test_x.py::TestA::test_b": NodeKind.TEST,
        "test:tests/test_x.py::test_c": NodeKind.TEST,
    }


def test_node_labels_locations_and_attributes(built: GraphBuild) -> None:
    graph = built.graph
    test_node = graph.node(n("test:tests/test_x.py::TestA::test_b"))
    assert test_node.label == "tests/test_x.py::TestA::test_b"
    assert test_node.location is not None
    assert (test_node.location.path, test_node.location.start_line) == ("tests/test_x.py", 3)
    assert dict(test_node.attributes) == {"symbol_kind": "test_method", "visibility": "public"}
    assert graph.node(n("py:pkg.m:Engine.go")).label == "Engine.go"
    assert graph.node(n("py:pkg.m")).label == "pkg.m"
    file_node = graph.node(n("file:pkg/m.py"))
    assert (file_node.label, file_node.location, dict(file_node.attributes)) == (
        "pkg/m.py",
        None,
        {},
    )


def test_edges_keep_origin_confidence_and_flags_and_are_renamed(built: GraphBuild) -> None:
    edges = _edge_set(built)
    certain, name_res = "certain", "name_resolution"
    # A test node is the source and the target of renamed edges.
    assert (
        "test:tests/test_x.py::TestA::test_b",
        "calls",
        "py:pkg.m:f",
        name_res,
        certain,
        frozenset(),
    ) in edges
    assert (
        "test:tests/test_x.py::test_c",
        "calls",
        "test:tests/test_x.py::TestA::test_b",
        name_res,
        "likely",
        frozenset(),
    ) in edges
    # The constant folds into its module.
    assert ("py:pkg.m:f", "references", "py:pkg.c", name_res, certain, frozenset()) in edges
    # Heuristic name match survives with its origin and confidence.
    assert (
        "py:pkg.m:g",
        "calls",
        "py:pkg.m:Engine.go",
        "heuristic",
        "possible",
        frozenset(),
    ) in edges
    # The TYPE_CHECKING import of pkg merges with the runtime import of pkg (from pkg.c import):
    # the dependency exists at runtime, so it is not type_checking_only.
    stored = built.graph.edge(n("py:pkg.m"), EdgeKind.IMPORTS, n("py:pkg"))
    assert stored is not None
    assert EdgeFlag.TYPE_CHECKING_ONLY not in stored.flags
    assert not any(node.id.startswith("py:pkg.c:") for node in built.graph.nodes())


def test_analyzer_unresolved_references_carry_over_renamed(built: GraphBuild) -> None:
    assert [(u.source, u.reason, u.raw) for u in built.unresolved] == [
        (n("py:pkg.m"), UnresolvedReason.EXTERNAL_MODULE, "typing"),
        (n("test:tests/test_x.py::test_c"), UnresolvedReason.UNKNOWN, "cb"),
    ]


def test_tests_reach_what_they_exercise(built: GraphBuild) -> None:
    reach = built.graph.reverse_reachable(n("py:pkg.m:f"), kinds={EdgeKind.CALLS})
    assert n("test:tests/test_x.py::test_c") in reach
    assert reach.path(n("test:tests/test_x.py::test_c")) == (
        n("py:pkg.m:f"),
        n("test:tests/test_x.py::TestA::test_b"),
        n("test:tests/test_x.py::test_c"),
    )


def test_edge_to_a_missing_node_becomes_unresolved_never_dropped(
    results: dict[str, AnalysisResult],
) -> None:
    partial = [r for path, r in results.items() if path != "pkg/c.py"]
    build = build_graph(partial)
    missing = {(u.source, u.reason, u.raw) for u in build.unresolved}
    assert (n("py:pkg.m"), UnresolvedReason.MISSING_MODULE, "py:pkg.c") in missing
    assert (n("py:pkg.m:f"), UnresolvedReason.MISSING_MODULE, "py:pkg.c:LIMIT") in missing
    ref = next(u for u in build.unresolved if u.raw == "py:pkg.c:LIMIT")
    assert (ref.location.path, ref.location.start_line) == ("pkg/m.py", 5)  # the source's span


def test_edge_to_a_missing_symbol_of_a_present_module_is_unknown(
    results: dict[str, AnalysisResult],
) -> None:
    m = results["pkg/m.py"]
    ghost = dataclasses.replace(m.edges[0], target=n("py:pkg.c:ghost"), kind=EdgeKind.CALLS)
    from_file = dataclasses.replace(
        m.edges[0], source=n("file:pkg/m.py"), target=n("py:pkg.m:nothere")
    )
    edges = sort_unique([*m.edges, ghost, from_file], edge_sort_key)
    others = [r for path, r in results.items() if path != "pkg/m.py"]
    build = build_graph([*others, dataclasses.replace(m, edges=edges)])
    found = {(u.source, u.reason, u.raw): u for u in build.unresolved}
    assert (n("py:pkg.m"), UnresolvedReason.UNKNOWN, "py:pkg.c:ghost") in found
    anchored = found[(n("file:pkg/m.py"), UnresolvedReason.UNKNOWN, "py:pkg.m:nothere")]
    assert (anchored.location.start_line, anchored.location.start_col) == (1, 0)  # top of file


def test_input_order_does_not_matter(results: dict[str, AnalysisResult]) -> None:
    forward = build_graph(sorted(results.values(), key=lambda r: r.file_path))
    backward = build_graph(sorted(results.values(), key=lambda r: r.file_path, reverse=True))
    assert forward.graph == backward.graph
    assert forward.unresolved == backward.unresolved
    assert forward.graph.to_dict() == backward.graph.to_dict()


def test_two_results_for_one_file_are_a_conflict(results: dict[str, AnalysisResult]) -> None:
    with pytest.raises(GraphError) as exc_info:
        build_graph([*results.values(), results["pkg/m.py"]])
    assert exc_info.value.code is ErrorCode.GRAPH_CONFLICT


def test_empty_input_builds_an_empty_graph() -> None:
    build = build_graph([])
    assert (build.graph.node_count, build.graph.edge_count, build.unresolved) == (0, 0, ())


RENAMED = {
    "py:tests.test_x:TestA.test_b": "test:tests/test_x.py::TestA::test_b",
    "py:tests.test_x:test_c": "test:tests/test_x.py::test_c",
    "py:pkg.c:LIMIT": "py:pkg.c",
}


def test_every_analyzer_edge_is_in_the_graph_unweakened(
    built: GraphBuild, results: dict[str, AnalysisResult]
) -> None:
    """Every analyzer edge appears with its kind and a confidence at least as strong (only
    duplicate merging may strengthen it); its endpoints change only by the documented renames."""
    count = 0
    for result in results.values():
        for edge in result.edges:
            source = n(RENAMED.get(edge.source, edge.source))
            target = n(RENAMED.get(edge.target, edge.target))
            stored = built.graph.edge(source, edge.kind, target)
            assert stored is not None, edge
            assert stored.confidence >= edge.confidence
            count += 1
    assert count >= built.graph.edge_count
