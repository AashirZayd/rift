"""Regression tests for the M4 fresh-context review findings (ADR-0010 amendment)."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rift.analysis.python.analyzer import ProjectAnalysis, analyze_project, analyze_sources
from rift.core.errors import ErrorCode
from rift.core.ids import NodeId
from rift.core.models.change import ChangedFile, ChangeStatus, LineRange
from rift.core.models.impact import Completeness, ImpactClass, ImpactResult
from rift.graph.builder import build_graph
from rift.impact.assess import ImpactSettings, assess_impact
from rift.impact.errors import ImpactError
from rift.impact.index import ProjectIndex
from rift.impact.mapping import map_changes, map_nodes
from tests.fixtures.python_project import write_project
from tests.unit.impact.helpers import analyze, impact_of_change, impact_of_nodes, modified

ORDER = list(ImpactClass)  # declaration order is the ordinal ladder: none < ... < public_api


def _change(
    analysis: ProjectAnalysis,
    files: list[ChangedFile],
    base: ProjectAnalysis | None = None,
    exclude: tuple[str, ...] = (),
) -> ImpactResult:
    index = ProjectIndex(analysis)
    mapping = map_changes(files, index, base=base, exclude=exclude)
    return assess_impact(
        analysis=analysis, build=build_graph(analysis.results), mapping=mapping, index=index
    )


def _file(
    path: str, status: ChangeStatus, *lines: int, old: str | None = None, binary: bool = False
) -> ChangedFile:
    return ChangedFile(
        path=path,
        old_path=old,
        status=status,
        changed_lines=tuple(LineRange(start=n, end=n) for n in lines),
        is_binary=binary,
    )


# ----------------------------------------------------------------------------- #1, #12


TWO_PACKAGES: dict[str, str | bytes] = {
    "a/__init__.py": "",
    "a/x.py": "def _h():\n    return 1\n",
    "b/__init__.py": "",
    "b/z.py": "from a.x import _h\n\n\ndef _g():\n    return _h()\n",
}


def test_changing_the_caller_too_never_lowers_the_tier(tmp_path: Path) -> None:
    """#1: a helper and its caller in another package both changed is still cross_package."""
    analysis = analyze(tmp_path, TWO_PACKAGES)
    alone = impact_of_change(analysis, [modified("a/x.py", 2)])
    both = impact_of_change(analysis, [modified("a/x.py", 2), modified("b/z.py", 5)])
    assert alone.classification is ImpactClass.CROSS_PACKAGE
    assert both.classification is ImpactClass.CROSS_PACKAGE


def test_unrelated_changes_in_two_packages_are_not_cross_package(tmp_path: Path) -> None:
    """#12: only the ends of crossing dependencies count, not every changed unit."""
    files: dict[str, str | bytes] = {
        "a/__init__.py": "",
        "a/x.py": "def _h():\n    return 1\n",
        "a/y.py": "from a.x import _h\ndef _u():\n    return _h()\n",
        "b/__init__.py": "",
        "b/z.py": "def _solo():\n    return 2\n",
    }
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("a/x.py", 2), modified("b/z.py", 2)])
    assert result.classification is ImpactClass.MODULE


LADDER_PROJECT: dict[str, str | bytes] = {
    "a/__init__.py": "",
    "a/x.py": (
        "def _h():\n    return 1\ndef pub():\n    return _h()\n"
        "class _K:\n    def m(self):\n        return pub()\n"
    ),
    "a/y.py": "from a.x import _h\ndef _u():\n    return _h()\n",
    "b/__init__.py": "",
    "b/z.py": "from a.y import _u\ndef _g():\n    return _u()\n",
    "tests/test_a.py": "from a.x import pub\ndef test_pub():\n    assert pub()\n",
}


@pytest.fixture(scope="module")
def ladder(tmp_path_factory: pytest.TempPathFactory) -> tuple[ProjectAnalysis, list[NodeId]]:
    analysis = analyze(tmp_path_factory.mktemp("ladder"), LADDER_PROJECT)
    index = ProjectIndex(analysis)
    nodes = sorted(
        n.id
        for n in build_graph(analysis.results).graph.nodes()
        if index.knows(n.id) and not n.id.startswith("file:")
    )
    return analysis, nodes


@settings(max_examples=60, deadline=None)
@given(data=st.data())
def test_tier_is_monotonic_in_the_change_set(
    ladder: tuple[ProjectAnalysis, list[NodeId]], data: st.DataObject
) -> None:
    """Adding changed nodes never lowers the tier (property behind #1)."""
    analysis, nodes = ladder
    small = data.draw(st.sets(st.sampled_from(nodes)))
    large = small | data.draw(st.sets(st.sampled_from(nodes)))
    low = impact_of_nodes(analysis, sorted(small)).classification
    high = impact_of_nodes(analysis, sorted(large)).classification
    assert ORDER.index(low) <= ORDER.index(high)


# ----------------------------------------------------------------------------- #2, #7


def _base(tmp_path: Path, files: dict[str, str]) -> ProjectAnalysis:
    return analyze_sources({p: c.encode() for p, c in files.items()}, source_roots=(".",))


def test_removed_public_symbol_is_public_api(tmp_path: Path) -> None:
    """#2: removing a public function from a modified file is visible from the base side."""
    analysis = analyze(
        tmp_path, {"pkg/__init__.py": "", "pkg/core.py": "def _helper():\n    return 1\n"}
    )
    base = _base(
        tmp_path, {"pkg/core.py": "def _helper():\n    return 1\ndef public_fn():\n    return 2\n"}
    )
    result = _change(analysis, [_file("pkg/core.py", ChangeStatus.MODIFIED, 2)], base)
    assert "py:pkg.core:public_fn" in result.changed_nodes
    assert result.classification is ImpactClass.PUBLIC_API


def test_removed_private_symbol_reaches_its_users_but_is_not_public(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/core.py": "X = 1\n",
        "pkg/user.py": "from pkg.core import _gone\ndef go():\n    return _gone()\n",
    }
    analysis = analyze(tmp_path, files)
    base = _base(tmp_path, {"pkg/core.py": "X = 1\ndef _gone():\n    return 1\n"})
    result = _change(analysis, [_file("pkg/core.py", ChangeStatus.MODIFIED, 1)], base)
    assert "py:pkg.core:_gone" in result.changed_nodes
    assert {"py:pkg.user", "py:pkg.user:go"} <= set(result.affected_nodes)
    assert result.classification is ImpactClass.PUBLIC_API  # X is public and changed


def test_rename_to_an_unanalyzed_path_is_a_deletion_of_the_old_path(tmp_path: Path) -> None:
    """#7: pkg/core.py -> pkg/core.py.bak while pkg/user.py still imports pkg.core."""
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/user.py": "from pkg.core import f\ndef go():\n    return f()\n",
    }
    analysis = analyze(tmp_path, files)
    moved = _file("pkg/core.py.bak", ChangeStatus.RENAMED, old="pkg/core.py")
    result = _change(analysis, [moved])
    assert result.changed_nodes == ("file:pkg/core.py",)
    assert result.completeness is Completeness.PARTIAL
    assert "py:pkg.user:go" in result.affected_nodes


# ----------------------------------------------------------------------------- #3, #11


def test_private_change_does_not_make_unrelated_importers_dependents(tmp_path: Path) -> None:
    """#3: pkg/user.py imports only OTHER from pkg.core; changing _helper stays local."""
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/core.py": "def _helper():\n    return 1\nOTHER = 2\n",
        "pkg/user.py": "from pkg.core import OTHER\nY = OTHER\n",
    }
    result = impact_of_change(analyze(tmp_path, files), [modified("pkg/core.py", 2)])
    assert result.affected_nodes == ()
    assert result.classification is ImpactClass.LOCAL


def test_module_level_change_still_makes_importers_dependents(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/core.py": "import os\n\n\ndef _helper():\n    return 1\n",
        "pkg/user.py": "import pkg.core\n",
    }
    result = impact_of_change(analyze(tmp_path, files), [modified("pkg/core.py", 1)])
    assert result.affected_nodes == ("py:pkg.user",)
    assert result.classification is ImpactClass.MODULE


def test_private_method_change_does_not_change_its_public_class(tmp_path: Path) -> None:
    """#11: the class counts as changed only for its own lines."""
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/m.py": "class Session:\n    limit = 3\n    def _m(self):\n        return 1\n",
    }
    analysis = analyze(tmp_path, files)
    method = impact_of_change(analysis, [modified("pkg/m.py", 4)])
    assert method.changed_nodes == ("py:pkg.m", "py:pkg.m:Session._m")
    assert method.classification is ImpactClass.LOCAL
    body = impact_of_change(analysis, [modified("pkg/m.py", 2)])  # a class-body statement
    assert "py:pkg.m:Session" in body.changed_nodes
    assert body.classification is ImpactClass.PUBLIC_API


# ----------------------------------------------------------------------------- #4, #5, #6


def test_all_decides_top_level_publicity(tmp_path: Path) -> None:
    """#4: names outside a literal __all__ are private; listed underscore names are public."""
    source = (
        "__all__ = ['foo', '_exported']\ndef foo():\n    pass\n"
        "def bar():\n    pass\ndef _exported():\n    pass\n"
    )
    analysis = analyze(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": source})
    assert impact_of_change(analysis, [modified("pkg/m.py", 5)]).classification is ImpactClass.LOCAL
    assert (
        impact_of_change(analysis, [modified("pkg/m.py", 7)]).classification
        is ImpactClass.PUBLIC_API
    )


def test_default_public_packages_are_under_the_source_roots_only(tmp_path: Path) -> None:
    """#5 and #6: in a src layout, tests/ is neither a public package nor production code."""
    files: dict[str, str | bytes] = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "def _helper():\n    return 1\n",
        "tests/conftest.py": "import pytest\n@pytest.fixture\ndef client():\n    return 1\n",
        "tests/helpers.py": "from pkg.core import _helper\ndef make():\n    return _helper()\n",
    }
    analysis = analyze(tmp_path, files)
    assert ProjectIndex(analysis).public_units() == ("pkg",)
    fixture = impact_of_change(analysis, [modified("tests/conftest.py", 4)])
    assert fixture.classification is ImpactClass.LOCAL
    assert fixture.public_interfaces == ()
    helper = impact_of_change(analysis, [modified("src/pkg/core.py", 2)])
    assert helper.classification is ImpactClass.LOCAL  # tests/helpers.py is test code
    assert helper.public_interfaces == ()


# ------------------------------------------------------------ #10, #14, #15, #17, #23


def test_non_failure_limitations_count_only_inside_the_impact_set(tmp_path: Path) -> None:
    """#10: a non-literal __all__ in an unrelated file does not make the result partial."""
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/core.py": "def _helper():\n    return 1\n",
        "pkg/other.py": "__all__ = ['a'] + []\na = 1\n",
    }
    analysis = analyze(tmp_path, files)
    unrelated = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert (unrelated.completeness, unrelated.limitations) == (Completeness.COMPLETE, ())
    inside = impact_of_change(analysis, [modified("pkg/other.py", 2)])
    assert [lim.kind for lim in inside.limitations] == ["non_literal_all"]


def test_deleted_excluded_file_maps_to_nothing(tmp_path: Path) -> None:
    """#14: exclusions apply to deleted paths like to modified ones."""
    analysis = analyze(tmp_path, {"pkg/__init__.py": ""})
    deleted = _file("vendor/lib.py", ChangeStatus.DELETED)
    result = _change(analysis, [deleted], exclude=("vendor/**",))
    assert (result.changed_nodes, result.limitations) == ((), ())


def test_map_nodes_rejects_invented_file_nodes(tmp_path: Path) -> None:
    """#15."""
    index = ProjectIndex(analyze(tmp_path, {"pkg/__init__.py": ""}))
    with pytest.raises(ImpactError) as exc_info:
        map_nodes([NodeId("file:does/not/exist.py")], index)
    assert exc_info.value.code is ErrorCode.UNKNOWN_NODE


@pytest.mark.parametrize("value", ["pkg", ["pkg"], ("pkg.sub",), ("",)])
def test_public_packages_are_validated(value: object) -> None:
    """#17: a string would silently become a set of characters."""
    with pytest.raises(ImpactError):
        ImpactSettings(public_packages=value)  # type: ignore[arg-type]  # deliberately malformed


@pytest.mark.parametrize(
    ("changed_file", "expected"),
    [
        pytest.param(
            _file("pkg/new.py", ChangeStatus.COPIED, 1, old="pkg/core.py"),
            {"py:pkg.new", "py:pkg.new:f", "py:pkg.new:_g"},
            id="copied-is-whole-file",
        ),
        pytest.param(
            _file("pkg/core.py", ChangeStatus.MODIFIED, binary=True),
            {"py:pkg.core", "py:pkg.core:f", "py:pkg.core:_g"},
            id="binary-is-whole-file",
        ),
        pytest.param(
            _file("pkg/core.py", ChangeStatus.TYPE_CHANGED, 4),
            {"py:pkg.core", "py:pkg.core:_g"},
            id="type-changed",
        ),
        pytest.param(
            _file("pkg/core.py", ChangeStatus.RENAMED, 2, old="pkg/old.py"),
            {"py:pkg.core", "py:pkg.core:f"},
            id="renamed-and-modified",
        ),
    ],
)
def test_change_kinds(tmp_path: Path, changed_file: ChangedFile, expected: set[str]) -> None:
    """#23: copied, binary, type-changed, renamed-and-modified files."""
    source = "def f():\n    return 1\ndef _g():\n    return 2\n"
    analysis = analyze(
        tmp_path, {"pkg/__init__.py": "", "pkg/core.py": source, "pkg/new.py": source}
    )
    assert set(_change(analysis, [changed_file]).changed_nodes) == expected


def test_changed_stub_is_a_completeness_reason(tmp_path: Path) -> None:
    """#23: .pyi files are not analyzed, so a stub-only API change is not silently none."""
    analysis = analyze(tmp_path, {"pkg/__init__.py": ""})
    result = _change(analysis, [_file("pkg/core.pyi", ChangeStatus.MODIFIED, 1)])
    assert result.classification is ImpactClass.NONE
    assert [(lim.kind, lim.subject) for lim in result.limitations] == [
        ("stub_not_analyzed", "pkg/core.pyi")
    ]


def test_base_snapshot_analysis_is_in_memory(tmp_path: Path) -> None:
    """analyze_sources parses bytes exactly like analyze_project parses files (nothing runs)."""
    source = "def f():\n    return 1\n"
    on_disk = analyze_project(write_project(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": source}))
    in_memory = analyze_sources(
        {"pkg/__init__.py": b"", "pkg/m.py": source.encode()}, source_roots=(".",)
    )
    assert on_disk.results == in_memory.results
