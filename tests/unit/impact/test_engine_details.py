"""ADR-0010 details: settings validation, dangling-edge anchors, deleted non-Python files,
unanalyzed paths, unknown nodes, and module names under source roots."""

from pathlib import Path

import pytest

from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.change import ChangedFile, ChangeStatus
from rift.core.models.graph import Confidence
from rift.graph.builder import build_graph
from rift.impact.assess import ImpactSettings, assess_impact
from rift.impact.errors import ImpactError
from rift.impact.index import ProjectIndex
from rift.impact.mapping import map_changes, map_nodes, map_paths
from tests.unit.impact.helpers import analyze, explanation, impact_of_change


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"min_confidence": "possible"}, id="string-confidence"),
        pytest.param({"max_depth": -1}, id="negative-depth"),
        pytest.param({"max_depth": True}, id="bool-depth"),
    ],
)
def test_settings_are_validated(kwargs: dict[str, object]) -> None:
    with pytest.raises(ImpactError):
        ImpactSettings(**kwargs)  # type: ignore[arg-type]  # deliberately malformed


def test_dangling_edges_into_a_removed_module_reach_the_change(tmp_path: Path) -> None:
    """Results from an older analysis can carry edges into a module whose file is gone (the
    graph keeps them as dangling); they link to the deleted file's node like references do."""
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/gone.py": "def thing():\n    return 1\n",
        "pkg/user.py": "from pkg.gone import thing\ndef go():\n    return thing()\n",
    }
    full = analyze(tmp_path, files)
    partial = type(full)(
        source_roots=full.source_roots,
        results=tuple(r for r in full.results if r.file_path != "pkg/gone.py"),
        limitations=full.limitations,
    )
    build = build_graph(partial.results)
    assert {str(e.target) for e in build.dangling} >= {"py:pkg.gone", "py:pkg.gone:thing"}
    deleted = ChangedFile(
        path="pkg/gone.py",
        old_path=None,
        status=ChangeStatus.DELETED,
        changed_lines=(),
        is_binary=False,
    )
    mapping = map_changes([deleted], ProjectIndex(partial))
    result = assess_impact(analysis=partial, build=build, mapping=mapping)
    assert {"py:pkg.user", "py:pkg.user:go"} <= set(result.affected_nodes)
    assert explanation(result, "py:pkg.user:go") == ("file:pkg/gone.py", "py:pkg.user:go")
    # The links carry the dangling edges' own confidence: a certain floor still keeps them.
    strict = assess_impact(
        analysis=partial,
        build=build,
        mapping=mapping,
        settings=ImpactSettings(min_confidence=Confidence.CERTAIN),
    )
    assert "py:pkg.user:go" in strict.affected_nodes


def test_deleted_non_python_file_maps_to_nothing(tmp_path: Path) -> None:
    analysis = analyze(tmp_path, {"pkg/__init__.py": ""})
    deleted = ChangedFile(
        path="docs/old.md",
        old_path=None,
        status=ChangeStatus.DELETED,
        changed_lines=(),
        is_binary=False,
    )
    result = impact_of_change(analysis, [deleted])
    assert (result.changed_nodes, result.limitations) == ((), ())


def test_unanalyzed_path_and_unknown_node_are_structured_errors(tmp_path: Path) -> None:
    index = ProjectIndex(analyze(tmp_path, {"pkg/__init__.py": ""}))
    with pytest.raises(ImpactError) as path_error:
        map_paths(["pkg/missing.py"], index)
    assert path_error.value.hint
    with pytest.raises(ImpactError) as node_error:
        map_nodes([NodeId("py:pkg:missing")], index)
    assert node_error.value.code is ErrorCode.UNKNOWN_NODE


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("src/pkg/gone.py", "pkg.gone", id="under-src"),
        pytest.param("src/pkg/__init__.py", "pkg", id="package-init"),
        pytest.param("tests/helpers.py", "tests.helpers", id="fallback-outside-roots"),
        pytest.param("src/my-dir/a.py", None, id="not-importable"),
    ],
)
def test_module_name_for_follows_the_analyzer(
    tmp_path: Path, path: str, expected: str | None
) -> None:
    """Review #13: the name a deleted or moved file had is the analyzer's name for it."""
    files: dict[str, str | bytes] = {
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": "",
        "tests/test_x.py": "",
    }
    index = ProjectIndex(analyze(tmp_path, files))
    assert index.source_roots == ("src",)
    assert index.module_name_for(path) == expected


def test_locals_segment_is_never_public(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/m.py": "def outer():\n    def inner():\n        return 1\n    return inner()\n",
    }
    index = ProjectIndex(analyze(tmp_path, files))
    assert not index.facts(NodeId("py:pkg.m:outer.<locals>.inner")).is_public
    assert index.facts(NodeId("py:pkg.m:outer")).is_public
