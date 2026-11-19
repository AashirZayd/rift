"""ADR-0010 §1 (M4 checklist): the analyzed file set is git's view (tracked + untracked-not-
ignored) intersected with discovery, assembled in rift.services. Real git, real files."""

import pytest

from rift.discovery.files import discover_python_files
from rift.git.files import list_worktree_files
from rift.git.repository import open_repository
from rift.services.analysis import analyze_repository, analyzed_file_set
from tests.fixtures.git_repo import GitRepo

pytestmark = pytest.mark.integration


@pytest.fixture
def project(repo: GitRepo) -> GitRepo:
    repo.write(".gitignore", "venv/\nbuild/\n*_generated.py\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "def run():\n    pass\n")
    repo.write("pkg/forced_generated.py", "X = 1\n")
    repo.commit_all("base")
    repo.git("add", "-f", "pkg/forced_generated.py")  # tracked although an ignore rule matches
    repo.commit_all("force-add")
    # A virtualenv without pyvenv.cfg: only .gitignore keeps it out.
    repo.write("venv/lib/python3.11/site-packages/evil.py", "import os\n")
    repo.write("build/lib/pkg/core.py", "def run():\n    pass\n")
    repo.write("pkg/new.py", "from pkg.core import run\n")  # untracked, not ignored
    repo.write("pkg/scratch_generated.py", "Y = 2\n")  # untracked and ignored
    (repo.root / "pkg" / "core.py").unlink()  # tracked, deleted from disk
    return repo


def test_ignored_and_deleted_files_are_never_analyzed(project: GitRepo) -> None:
    repository = open_repository(project.root)
    file_set = analyzed_file_set(repository)
    assert file_set.files == ("pkg/__init__.py", "pkg/forced_generated.py", "pkg/new.py")
    analysis = analyze_repository(repository)
    assert [r.file_path for r in analysis.results] == list(file_set.files)


def test_positive_control_discovery_alone_would_read_ignored_files(project: GitRepo) -> None:
    """Without git, the ignored venv and build output are on disk and discoverable."""
    discovered = discover_python_files(project.root).files
    assert "venv/lib/python3.11/site-packages/evil.py" in discovered
    assert "build/lib/pkg/core.py" in discovered
    assert "pkg/scratch_generated.py" in discovered
    listed = list_worktree_files(open_repository(project.root))
    assert "pkg/core.py" in listed  # still tracked: git lists it, discovery drops it
    assert not any(path.startswith(("venv/", "build/")) for path in listed)


def test_exclusions_still_apply_on_top_of_git(project: GitRepo) -> None:
    repository = open_repository(project.root)
    file_set = analyzed_file_set(repository, exclude=("pkg/new.py",))
    assert file_set.files == ("pkg/__init__.py", "pkg/forced_generated.py")


def test_untracked_nested_repository_is_not_part_of_this_one(project: GitRepo) -> None:
    nested = GitRepo.init(project.root / "vendor" / "other")
    nested.write("lib.py", "Z = 3\n")
    nested.commit_all("nested")
    listed = list_worktree_files(open_repository(project.root))
    assert not any(path.startswith("vendor/") for path in listed)
    assert "vendor/other/lib.py" in discover_python_files(project.root).files  # control
    assert analyzed_file_set(open_repository(project.root)).files == (
        "pkg/__init__.py",
        "pkg/forced_generated.py",
        "pkg/new.py",
    )
