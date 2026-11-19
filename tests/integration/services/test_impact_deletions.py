"""Deletions with the base-side parse (user decision 2) and broken internal imports (decision 1),
reproducing the review of those decisions with real git."""

import pytest

from rift.analysis.python.options import AnalysisOptions
from rift.core.models.graph import UnresolvedReason
from rift.core.models.impact import Completeness, ImpactClass, ImpactResult
from rift.services.impact import ImpactOptions, impact_of_changes
from tests.fixtures.git_repo import GitRepo

pytestmark = pytest.mark.integration


def _impact(repo: GitRepo, base: str, **analysis: object) -> ImpactResult:
    options = ImpactOptions(analysis=AnalysisOptions(**analysis))  # type: ignore[arg-type]  # test helper
    return impact_of_changes(repo.root, base=base, options=options).impact


def _reasons(result: ImpactResult) -> list[tuple[str, str | None]]:
    return [(lim.kind, lim.subject) for lim in result.limitations]


def _commit(repo: GitRepo, files: dict[str, str]) -> str:
    for path, content in files.items():
        repo.write(path, content)
    return repo.commit_all("base")


def test_deleting_a_still_imported_top_level_module_is_partial_and_public_api(
    repo: GitRepo,
) -> None:
    """Review S1: the head analyzer labels `import helpers` external once helpers.py is gone; it
    is a broken internal import, so it counts, and the deleted public h is public API."""
    base = _commit(
        repo,
        {
            "src/helpers.py": "def h():\n    return 1\n",
            "src/app/__init__.py": "",
            "src/app/use.py": "import helpers\n\n\ndef run():\n    return helpers.h()\n",
        },
    )
    (repo.root / "src" / "helpers.py").unlink()
    impact = _impact(repo, base)
    assert impact.changed_nodes == ("py:helpers", "py:helpers:h")
    assert {(u.reason, u.raw) for u in impact.unresolved} == {
        (UnresolvedReason.EXTERNAL_MODULE, "helpers")
    }
    assert impact.completeness is Completeness.PARTIAL
    assert _reasons(impact) == [("unresolved_reference", "external_module")]
    assert {"py:app.use", "py:app.use:run"} <= set(impact.affected_nodes)
    assert impact.classification is ImpactClass.PUBLIC_API


def test_deleting_a_pytest_prepend_helper_relates_the_broken_test(repo: GitRepo) -> None:
    """Review S2: tests/test_x.py imports tests/helpers.py as `helpers` (pytest prepend)."""
    base = _commit(
        repo,
        {
            "src/app/__init__.py": "",
            "tests/helpers.py": "def make():\n    return 1\n",
            "tests/test_x.py": "from helpers import make\n\n\ndef test_x():\n    assert make()\n",
        },
    )
    (repo.root / "tests" / "helpers.py").unlink()
    impact = _impact(repo, base)
    assert impact.changed_nodes == ("py:tests.helpers", "py:tests.helpers:make")
    assert impact.related_tests == ("test:tests/test_x.py::test_x",)
    assert impact.completeness is Completeness.PARTIAL


def test_deleted_test_file_is_changed_not_related(repo: GitRepo) -> None:
    """Review S3: a test that no longer exists cannot be selected."""
    base = _commit(
        repo,
        {"src/app/__init__.py": "", "tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    (repo.root / "tests" / "test_x.py").unlink()
    impact = _impact(repo, base)
    assert "test:tests/test_x.py::test_x" in impact.changed_nodes
    assert impact.related_tests == ()
    assert impact.classification is ImpactClass.LOCAL  # test code only


def test_deleting_a_public_reexport_shim_is_public_api(repo: GitRepo) -> None:
    """Review S4: the module defines no public symbol of its own, but its path was importable."""
    base = _commit(
        repo,
        {
            "src/app/__init__.py": "",
            "src/app/core.py": "def compute():\n    return 1\n",
            "src/app/shim.py": "from app.core import compute\n__all__ = ['compute']\n",
        },
    )
    (repo.root / "src" / "app" / "shim.py").unlink()
    impact = _impact(repo, base)
    assert impact.changed_nodes == ("py:app.shim",)
    assert impact.classification is ImpactClass.PUBLIC_API
    assert impact.completeness is Completeness.COMPLETE


def test_deleted_package_init_maps_to_the_package(repo: GitRepo) -> None:
    base = _commit(repo, {"src/app/__init__.py": "VERSION = '1'\n", "src/app/core.py": "X = 1\n"})
    (repo.root / "src" / "app" / "__init__.py").unlink()
    impact = _impact(repo, base)
    assert {"py:app", "py:app:VERSION"} <= set(impact.changed_nodes)
    assert impact.classification is ImpactClass.PUBLIC_API


def test_oversized_base_blob_falls_back_to_the_file_node(repo: GitRepo) -> None:
    base = _commit(repo, {"src/app/__init__.py": "", "src/app/big.py": "x = 1\n" * 100})
    (repo.root / "src" / "app" / "big.py").unlink()
    impact = _impact(repo, base, max_file_bytes=200)
    assert impact.changed_nodes == ("file:src/app/big.py",)
    assert _reasons(impact) == [("deleted_without_analysis", "src/app/big.py")]
    assert impact.classification is ImpactClass.PUBLIC_API  # symbols unknown, public path


def test_rename_out_of_the_analyzed_set_uses_the_base_side(repo: GitRepo) -> None:
    base = _commit(
        repo,
        {
            "src/app/__init__.py": "",
            "src/app/core.py": "def compute():\n    return 1\n",
            "src/app/user.py": (
                "from app.core import compute\n\n\ndef go():\n    return compute()\n"
            ),
        },
    )
    repo.git("mv", "src/app/core.py", "src/app/core.py.bak")
    impact = _impact(repo, base)
    assert impact.changed_nodes == ("py:app.core", "py:app.core:compute")
    assert "py:app.user:go" in impact.affected_nodes
    assert impact.classification is ImpactClass.PUBLIC_API
    assert _reasons(impact) == [("unresolved_reference", "missing_module")]
