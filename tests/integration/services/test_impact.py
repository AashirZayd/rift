"""End to end with real git (SPEC §11, §24 M4): a repository, a change, then git → analysis →
graph → ImpactResult, with the exact tier, completeness, affected nodes and related tests. Also
§11 step 1 for each kind of change: add, modify, delete, rename, whitespace-only."""

import pytest

from rift.core.models.impact import Completeness, ImpactClass, ImpactResult
from rift.services.impact import (
    ChangeImpact,
    impact_of_changes,
    impact_of_nodes,
    impact_of_paths,
    resolve_symbol,
)
from tests.fixtures.git_repo import GitRepo

pytestmark = pytest.mark.integration

CORE = "def _helper():\n    return 1\n\n\ndef compute():\n    return _helper()\n"
FILES = {
    ".gitignore": "venv/\n",
    "docs/readme.md": "docs\n",
    "src/app/__init__.py": "",
    "src/app/core.py": CORE,  # _helper: 1-2, blank: 3-4, compute: 5-6
    "src/app/api.py": "from app.core import compute\n\n\ndef handle():\n    return compute()\n",
    "src/lib/__init__.py": "",
    "src/lib/util.py": "from app.api import handle\n\n\ndef run():\n    return handle()\n",
    "tests/test_core.py": (
        "from app.core import compute\n\n\ndef test_compute():\n    assert compute() == 1\n"
    ),
}
TEST = "test:tests/test_core.py::test_compute"


@pytest.fixture
def project(repo: GitRepo) -> tuple[GitRepo, str]:
    for path, content in FILES.items():
        repo.write(path, content)
    return repo, repo.commit_all("base")


def _impact(project: tuple[GitRepo, str]) -> ChangeImpact:
    repo, base = project
    return impact_of_changes(repo.root, base=base)


def _reasons(result: ImpactResult) -> list[tuple[str, str | None]]:
    return [(lim.kind, lim.subject) for lim in result.limitations]


def test_end_to_end_private_change_crossing_packages(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    repo.write("src/app/core.py", CORE.replace("return 1", "return 2"))
    outcome = _impact(project)
    assert [(f.path, f.status.value) for f in outcome.changes.files] == [
        ("src/app/core.py", "modified")
    ]
    impact = outcome.impact
    assert impact.changed_nodes == ("py:app.core", "py:app.core:_helper")
    # Only code that uses the changed symbol: a symbol-only edit does not make every importer of
    # the module a dependent (ADR-0010 §4).
    assert impact.affected_nodes == (
        "py:app.api:handle",
        "py:app.core:compute",
        "py:lib.util:run",
        TEST,
    )
    assert impact.related_tests == (TEST,)
    assert impact.public_interfaces == (
        "py:app.api:handle",
        "py:app.core:compute",
        "py:lib.util:run",
    )
    assert impact.classification is ImpactClass.CROSS_PACKAGE
    assert (impact.completeness, impact.limitations, impact.unresolved) == (
        Completeness.COMPLETE,
        (),
        (),
    )
    paths = {str(e.node): e.path for e in impact.explanations}
    assert paths[TEST] == ("py:app.core:_helper", "py:app.core:compute", TEST)
    assert paths["py:lib.util:run"] == (
        "py:app.core:_helper",
        "py:app.core:compute",
        "py:app.api:handle",
        "py:lib.util:run",
    )


def test_added_untracked_file_maps_every_symbol(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    repo.write(
        "src/app/extra.py", "from app.core import _helper\n\n\ndef extra():\n    return _helper()\n"
    )
    impact = _impact(project).impact
    assert impact.changed_nodes == ("py:app.extra", "py:app.extra:extra")
    assert impact.affected_nodes == ()
    assert impact.classification is ImpactClass.PUBLIC_API


def test_whitespace_only_edit_inside_a_symbol_changes_that_symbol(
    project: tuple[GitRepo, str],
) -> None:
    repo, _ = project
    repo.write("src/app/core.py", CORE.replace("    return 1\n", "    return 1   \n"))
    impact = _impact(project).impact
    assert impact.changed_nodes == ("py:app.core", "py:app.core:_helper")
    assert impact.classification is ImpactClass.CROSS_PACKAGE


def test_whitespace_only_edit_between_symbols_changes_the_module_only(
    project: tuple[GitRepo, str],
) -> None:
    repo, _ = project
    repo.write("src/app/core.py", CORE.replace("\n\n\ndef compute", "\n  \n\ndef compute"))
    impact = _impact(project).impact
    assert impact.changed_nodes == ("py:app.core",)
    # Importers of the module, transitively: app.api imports it and lib.util imports app.api.
    assert impact.affected_nodes == ("py:app.api", "py:lib.util", "py:tests.test_core")
    assert impact.related_tests == ()  # no test calls a changed symbol
    assert impact.classification is ImpactClass.CROSS_PACKAGE


def test_deleted_module_maps_to_its_base_side_nodes(project: tuple[GitRepo, str]) -> None:
    """User decision: a deleted file is parsed on the base side, so its symbols are known."""
    repo, _ = project
    (repo.root / "src" / "app" / "core.py").unlink()
    impact = _impact(project).impact
    assert impact.changed_nodes == ("py:app.core", "py:app.core:_helper", "py:app.core:compute")
    # Partial only because code still imports the deleted module (missing_module), not because
    # the deletion was unanalyzable.
    assert impact.completeness is Completeness.PARTIAL
    assert _reasons(impact) == [("unresolved_reference", "missing_module")]
    # Code that still imports the deleted module reaches the change; a failed module-level
    # import affects every symbol of the importing file, so the test is related (review #8).
    assert impact.affected_nodes == (
        "py:app.api",
        "py:app.api:handle",
        "py:lib.util",
        "py:lib.util:run",
        "py:tests.test_core",
        TEST,
    )
    assert impact.related_tests == (TEST,)
    paths = {str(e.node): e.path for e in impact.explanations}
    assert paths[TEST] == ("py:app.core", TEST)
    assert paths["py:lib.util"] == ("py:app.core", "py:app.api", "py:lib.util")
    # The deleted public function compute is public API removed.
    assert "py:app.core:compute" in impact.public_interfaces
    assert impact.classification is ImpactClass.PUBLIC_API


def test_deleted_module_without_users_is_complete(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    repo.write("src/app/spare.py", "def _unused():\n    return 1\n")
    base = repo.commit_all("spare")
    (repo.root / "src" / "app" / "spare.py").unlink()
    impact = impact_of_changes(repo.root, base=base).impact
    assert impact.changed_nodes == ("py:app.spare", "py:app.spare:_unused")
    assert (impact.completeness, impact.limitations) == (Completeness.COMPLETE, ())
    assert impact.classification is ImpactClass.LOCAL  # its only symbol was private


def test_deleted_file_whose_base_side_does_not_parse_falls_back(
    project: tuple[GitRepo, str],
) -> None:
    repo, _ = project
    repo.write("src/app/broken.py", "def f(:\n")
    base = repo.commit_all("broken")
    (repo.root / "src" / "app" / "broken.py").unlink()
    impact = impact_of_changes(repo.root, base=base).impact
    assert impact.changed_nodes == ("file:src/app/broken.py",)
    assert _reasons(impact) == [("deleted_without_analysis", "src/app/broken.py")]
    # Symbols unknown: a public module path is assumed to be public API.
    assert impact.classification is ImpactClass.PUBLIC_API


def test_renamed_module_maps_under_its_new_path(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    repo.git("mv", "src/app/api.py", "src/app/endpoints.py")
    outcome = _impact(project)
    (changed_file,) = outcome.changes.files
    assert (changed_file.status.value, changed_file.old_path, changed_file.path) == (
        "renamed",
        "src/app/api.py",
        "src/app/endpoints.py",
    )
    impact = outcome.impact
    # No line changed, but the base side shows that public app.api:handle no longer exists at
    # its old path (review #2): the rename removed public API.
    assert impact.changed_nodes == ("py:app.api:handle", "py:app.endpoints")
    # lib.util still imports the old name: it and its code reach the renamed module.
    assert impact.affected_nodes == ("py:lib.util", "py:lib.util:run")
    assert _reasons(impact) == [("unresolved_reference", "missing_module")]
    assert impact.classification is ImpactClass.PUBLIC_API


def test_non_source_and_ignored_changes_are_none(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    repo.write("docs/readme.md", "changed\n")
    repo.write("venv/lib/site.py", "x = 1\n")  # ignored: never listed, never analyzed
    impact = _impact(project).impact
    assert (impact.classification, impact.changed_nodes, impact.completeness) == (
        ImpactClass.NONE,
        (),
        Completeness.COMPLETE,
    )


def test_symbol_and_path_services(project: tuple[GitRepo, str]) -> None:
    repo, _ = project
    lookup = resolve_symbol(repo.root, "compute")
    assert lookup.match == "py:app.core:compute"
    by_symbol = impact_of_nodes(repo.root, [lookup.match])
    assert by_symbol.changed_nodes == ("py:app.core:compute",)
    assert by_symbol.related_tests == (TEST,)
    assert by_symbol.classification is ImpactClass.PUBLIC_API
    by_path = impact_of_paths(repo.root, ["src/lib/util.py"])
    assert by_path.changed_nodes == ("py:lib.util", "py:lib.util:run")
    assert by_path.classification is ImpactClass.PUBLIC_API


def test_removing_a_public_function_from_a_modified_file_is_public_api(
    project: tuple[GitRepo, str],
) -> None:
    """Review #2: the base side (parsed in memory, never executed) shows compute is gone."""
    repo, _ = project
    repo.write("src/app/core.py", "def _helper():\n    return 1\n")
    impact = _impact(project).impact
    assert "py:app.core:compute" in impact.changed_nodes
    assert "py:app.core:compute" in impact.public_interfaces
    # Its users fail to import it: they, and the test, are affected.
    assert {"py:app.api", "py:app.api:handle", TEST} <= set(impact.affected_nodes)
    assert impact.related_tests == (TEST,)
    assert impact.classification is ImpactClass.PUBLIC_API
    assert impact.completeness is Completeness.PARTIAL  # the now-missing import is unresolved
