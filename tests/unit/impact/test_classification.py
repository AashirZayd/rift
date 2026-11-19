"""SPEC §11.1 / ADR-0010 §7: one test per tier, plus the boundary between each pair of
neighbouring tiers (none|local, local|module, module|cross_package, cross_package|public_api).
Tiers are ordinal; nothing here is a probability."""

from pathlib import Path

import pytest

import rift.core.models.impact
import rift.impact.assess
import rift.impact.classify
from rift.core.models.impact import ImpactClass
from rift.impact.assess import ImpactSettings
from tests.unit.impact.helpers import analyze, impact_of_change, modified

CORE = """
    def _helper():
        return 1
    def compute():
        return _helper()
"""  # _helper: lines 1-2, compute: lines 3-4
USER_IN_PKG = """
    from pkg.core import _helper
    def go():
        return _helper()
"""
TEST_HELPER = "from pkg.core import _helper\ndef test_it():\n    assert _helper()\n"
TEST_COMPUTE = "from pkg.core import compute\ndef test_it():\n    assert compute()\n"
USER_IN_OTHER = """
    from pkg.core import _helper
    def run():
        return _helper()
"""


def _project(**extra: str) -> dict[str, str | bytes]:
    files: dict[str, str | bytes] = {"pkg/__init__.py": "", "pkg/core.py": CORE, "README.md": "x"}
    files.update(extra)
    return files


def _tier(
    tmp_path: Path,
    files: dict[str, str | bytes],
    path: str,
    *lines: int,
    public_packages: tuple[str, ...] | None = None,
) -> ImpactClass:
    analysis = analyze(tmp_path, files)
    settings = ImpactSettings(public_packages=public_packages)
    return impact_of_change(analysis, [modified(path, *lines)], settings).classification


# ----------------------------------------------------------------------------- one test per tier


def test_none_no_changed_file_maps_to_a_node(tmp_path: Path) -> None:
    analysis = analyze(tmp_path, _project())
    result = impact_of_change(analysis, [modified("README.md", 1)])
    assert (result.classification, result.changed_nodes, result.affected_nodes) == (
        ImpactClass.NONE,
        (),
        (),
    )


def test_local_private_change_with_dependents_only_in_its_own_module(tmp_path: Path) -> None:
    analysis = analyze(tmp_path, _project())
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert result.changed_nodes == ("py:pkg.core", "py:pkg.core:_helper")
    assert result.affected_nodes == ("py:pkg.core:compute",)  # same module: still local
    assert result.classification is ImpactClass.LOCAL


def test_module_dependents_outside_the_module_within_one_package(tmp_path: Path) -> None:
    assert _tier(tmp_path, _project(**{"pkg/user.py": USER_IN_PKG}), "pkg/core.py", 2) is (
        ImpactClass.MODULE
    )


def test_cross_package_dependents_in_another_top_level_package(tmp_path: Path) -> None:
    files = _project(**{"other/__init__.py": "", "other/use.py": USER_IN_OTHER})
    assert _tier(tmp_path, files, "pkg/core.py", 2) is ImpactClass.CROSS_PACKAGE


def test_public_api_a_changed_public_symbol_in_a_public_package(tmp_path: Path) -> None:
    assert _tier(tmp_path, _project(), "pkg/core.py", 4) is ImpactClass.PUBLIC_API


# ----------------------------------------------------------------------------- boundaries


def test_boundary_none_local(tmp_path: Path) -> None:
    """A non-source change is none; the smallest source change is local."""
    files = _project()
    assert _tier(tmp_path / "a", files, "README.md", 1) is ImpactClass.NONE
    assert _tier(tmp_path / "b", files, "pkg/core.py", 2) is ImpactClass.LOCAL


def test_boundary_local_module(tmp_path: Path) -> None:
    """The same private change: one dependent outside its module turns local into module."""
    assert _tier(tmp_path / "a", _project(), "pkg/core.py", 2) is ImpactClass.LOCAL
    with_user = _project(**{"pkg/user.py": USER_IN_PKG})
    assert _tier(tmp_path / "b", with_user, "pkg/core.py", 2) is ImpactClass.MODULE


def test_boundary_module_cross_package(tmp_path: Path) -> None:
    """The same dependent code, inside the package versus in a second top-level package."""
    inside = _project(**{"pkg/use.py": USER_IN_OTHER})
    assert _tier(tmp_path / "a", inside, "pkg/core.py", 2) is ImpactClass.MODULE
    outside = _project(**{"other/__init__.py": "", "other/use.py": USER_IN_OTHER})
    assert _tier(tmp_path / "b", outside, "pkg/core.py", 2) is ImpactClass.CROSS_PACKAGE


def test_boundary_cross_package_public_api(tmp_path: Path) -> None:
    """Same project with cross-package dependents: a private change stays cross_package, a
    public one is public_api."""
    files = _project(**{"other/__init__.py": "", "other/use.py": USER_IN_OTHER})
    assert _tier(tmp_path / "a", files, "pkg/core.py", 2) is ImpactClass.CROSS_PACKAGE
    assert _tier(tmp_path / "b", files, "pkg/core.py", 4) is ImpactClass.PUBLIC_API


# ----------------------------------------------------------------------------- rule details


def test_public_packages_setting_limits_public_api(tmp_path: Path) -> None:
    uses_compute = "from pkg.core import compute\ndef run():\n    return compute()\n"
    files = _project(**{"other/__init__.py": "", "other/use.py": uses_compute})
    assert _tier(tmp_path / "a", files, "pkg/core.py", 4, public_packages=("other",)) is (
        ImpactClass.CROSS_PACKAGE
    )
    # A public symbol outside the public packages, with no outside dependents, stays local.
    assert _tier(tmp_path / "b", _project(), "pkg/core.py", 4, public_packages=()) is (
        ImpactClass.LOCAL
    )


@pytest.mark.parametrize(
    ("files", "path", "line", "expected"),
    [
        pytest.param(
            {"pkg/__init__.py": "", "pkg/_internal.py": "def visible():\n    return 1\n"},
            "pkg/_internal.py",
            2,
            ImpactClass.LOCAL,
            id="private-module-segment",
        ),
        pytest.param(
            {
                "pkg/__init__.py": "",
                "pkg/m.py": "class _Impl:\n    def run(self):\n        return 1\n",
            },
            "pkg/m.py",
            3,
            ImpactClass.LOCAL,
            id="public-method-of-private-class",
        ),
        pytest.param(
            {
                "pkg/__init__.py": "",
                "pkg/m.py": "class Session:\n    def __init__(self):\n        self.x = 1\n",
            },
            "pkg/m.py",
            3,
            ImpactClass.PUBLIC_API,
            id="dunder-method-is-public",
        ),
        pytest.param(
            {"pkg/__init__.py": "", "pkg/m.py": "LIMIT = 3\n_SECRET = 4\n"},
            "pkg/m.py",
            1,
            ImpactClass.PUBLIC_API,
            id="public-constant",
        ),
        pytest.param(
            {"pkg/__init__.py": "", "pkg/m.py": "LIMIT = 3\n_SECRET = 4\n"},
            "pkg/m.py",
            2,
            ImpactClass.LOCAL,
            id="private-constant",
        ),
        pytest.param(
            {"pkg/__init__.py": "", "pkg/m.py": "X = 1\n\n\ndef f():\n    return X\n"},
            "pkg/m.py",
            2,
            ImpactClass.LOCAL,
            id="module-level-line-only",
        ),
    ],
)
def test_publicity_rules(
    tmp_path: Path, files: dict[str, str | bytes], path: str, line: int, expected: ImpactClass
) -> None:
    assert _tier(tmp_path, files, path, line) is expected


def test_test_code_is_not_a_dependent_but_is_a_related_test(tmp_path: Path) -> None:
    files = _project(**{"tests/test_core.py": TEST_HELPER})
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert result.classification is ImpactClass.LOCAL
    assert result.related_tests == ("test:tests/test_core.py::test_it",)


def test_test_only_change_is_local_and_the_test_is_related(tmp_path: Path) -> None:
    files = _project(**{"tests/test_core.py": TEST_COMPUTE})
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("tests/test_core.py", 3)])
    assert result.classification is ImpactClass.LOCAL
    assert "test:tests/test_core.py::test_it" in result.changed_nodes
    assert result.related_tests == ("test:tests/test_core.py::test_it",)


def test_no_probability_language_in_the_impact_engine() -> None:
    """The tier is ordinal: no code or docstring presents it as a risk probability or score."""
    for module in (rift.impact.classify, rift.impact.assess, rift.core.models.impact):
        source = Path(module.__file__ or "").read_text(encoding="utf-8").lower()
        for line in source.splitlines():
            if any(word in line for word in ("probabil", "risk", "score", "likelihood")):
                assert any(neg in line for neg in ("not", "never", "nothing")), line
