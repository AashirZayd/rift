"""SPEC §11 / ADR-0010 §6, §8: completeness is orthogonal to the tier and every reason is listed;
related tests are the test nodes reached, each with an explanation path, honouring
min_confidence and max_depth."""

from pathlib import Path

from rift.core.models.change import ChangedFile, ChangeStatus
from rift.core.models.graph import Confidence
from rift.core.models.impact import Completeness, ImpactClass
from rift.impact.assess import ImpactSettings
from tests.unit.impact.helpers import (
    analyze,
    explanation,
    impact_of_change,
    impact_of_nodes,
    modified,
)

CORE = "def _helper():\n    return 1\ndef compute():\n    return _helper()\n"
BASE: dict[str, str | bytes] = {"pkg/__init__.py": "", "pkg/core.py": CORE}


def _reasons(
    files: dict[str, str | bytes], tmp_path: Path, **options: object
) -> list[tuple[str, str | None]]:
    analysis = analyze(tmp_path, files, **options)
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert (result.completeness is Completeness.COMPLETE) == (not result.limitations)
    return [(lim.kind, lim.subject) for lim in result.limitations]


def test_complete_when_nothing_is_missing(tmp_path: Path) -> None:
    assert _reasons(BASE, tmp_path) == []


def test_a_file_that_failed_to_parse_anywhere_makes_it_partial(tmp_path: Path) -> None:
    files = {**BASE, "other/broken.py": "def x(:\n"}
    assert _reasons(files, tmp_path) == [("analysis_failed", "other/broken.py")]


def test_a_skipped_file_makes_it_partial(tmp_path: Path) -> None:
    files = {**BASE, "other/big.py": "x = 1\n" * 100}
    assert _reasons(files, tmp_path, max_file_bytes=200) == [("oversized_file", "other/big.py")]


def test_an_unresolved_reference_touching_the_impact_set_makes_it_partial(tmp_path: Path) -> None:
    core = "def _helper():\n    return 1\ndef compute(cb):\n    _helper()\n    return cb()\n"
    analysis = analyze(tmp_path, {**BASE, "pkg/core.py": core})
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert result.completeness is Completeness.PARTIAL
    assert [(u.source, u.raw) for u in result.unresolved] == [("py:pkg.core:compute", "cb")]
    (reason,) = result.limitations
    assert (reason.kind, reason.subject) == ("unresolved_reference", "unknown")
    assert "1 unresolved reference(s)" in reason.message
    assert result.classification is ImpactClass.LOCAL  # orthogonal to the tier


def test_an_unresolved_reference_outside_the_impact_set_does_not(tmp_path: Path) -> None:
    files = {**BASE, "other/__init__.py": "", "other/x.py": "def f(cb):\n    return cb()\n"}
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert (result.completeness, result.unresolved) == (Completeness.COMPLETE, ())


def test_deleted_file_is_its_file_node_only_and_partial(tmp_path: Path) -> None:
    files = {**BASE, "pkg/user.py": "from pkg.gone import thing\ndef go():\n    return thing()\n"}
    analysis = analyze(tmp_path, files)
    deleted = ChangedFile(
        path="pkg/gone.py",
        old_path=None,
        status=ChangeStatus.DELETED,
        changed_lines=(),
        is_binary=False,
    )
    result = impact_of_change(analysis, [deleted])
    assert result.changed_nodes == ("file:pkg/gone.py",)
    assert result.completeness is Completeness.PARTIAL
    assert [(lim.kind, lim.subject) for lim in result.limitations] == [
        ("deleted_without_analysis", "pkg/gone.py"),
        ("unresolved_reference", "missing_module"),
    ]
    # The importer of the deleted module still reaches the change (likely link, ADR-0010 §5).
    assert "py:pkg.user" in result.affected_nodes
    assert explanation(result, "py:pkg.user") == ("file:pkg/gone.py", "py:pkg.user")
    assert result.classification is ImpactClass.MODULE


def test_every_reason_is_listed_together(tmp_path: Path) -> None:
    core = "def _helper():\n    return 1\ndef compute(cb):\n    _helper()\n    return cb()\n"
    files = {**BASE, "pkg/core.py": core, "other/broken.py": "def x(:\n"}
    analysis = analyze(tmp_path, files)
    deleted = ChangedFile(
        path="pkg/gone.py",
        old_path=None,
        status=ChangeStatus.DELETED,
        changed_lines=(),
        is_binary=False,
    )
    result = impact_of_change(analysis, [modified("pkg/core.py", 2), deleted])
    assert [lim.kind for lim in result.limitations] == [
        "analysis_failed",
        "deleted_without_analysis",
        "unresolved_reference",
    ]


# ----------------------------------------------------------------------------- related tests

ENGINE = "class Engine:\n    def crank(self):\n        return 1\n"
TESTS = (
    "from pkg.engine import Engine\n"
    "def test_direct():\n    Engine().crank()\n"
    "def test_by_name(obj):\n    obj.crank()\n"
    "def test_unrelated():\n    pass\n"
)


def test_related_tests_honour_min_confidence_with_paths(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/engine.py": ENGINE,
        "tests/test_engine.py": TESTS,
    }
    analysis = analyze(tmp_path, files)
    method = "py:pkg.engine:Engine.crank"
    possible = impact_of_nodes(analysis, [method])
    assert possible.related_tests == (
        "test:tests/test_engine.py::test_by_name",
        "test:tests/test_engine.py::test_direct",
    )
    assert explanation(possible, "test:tests/test_engine.py::test_by_name") == (
        method,
        "test:tests/test_engine.py::test_by_name",
    )
    likely = impact_of_nodes(analysis, [method], ImpactSettings(min_confidence=Confidence.LIKELY))
    assert likely.related_tests == ("test:tests/test_engine.py::test_direct",)


def test_max_depth_limits_the_traversal(tmp_path: Path) -> None:
    chain = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def a():\n    return 1\n",
        "pkg/b.py": "from pkg.a import a\ndef b():\n    return a()\n",
        "pkg/c.py": "from pkg.b import b\ndef c():\n    return b()\n",
    }
    analysis = analyze(tmp_path, chain)
    shallow = impact_of_nodes(analysis, ["py:pkg.a:a"], ImpactSettings(max_depth=1))
    deep = impact_of_nodes(analysis, ["py:pkg.a:a"])
    assert "py:pkg.c:c" not in shallow.affected_nodes
    assert "py:pkg.c:c" in deep.affected_nodes
    assert explanation(deep, "py:pkg.c:c") == ("py:pkg.a:a", "py:pkg.b:b", "py:pkg.c:c")
