"""Repository discovery, current revision, revision validation and base resolution (real git)."""

from pathlib import Path

import pytest

from rift.core.errors import ErrorCode
from rift.git.errors import (
    GitError,
    InvalidRevisionError,
    NotARepositoryError,
    ShallowHistoryError,
)
from rift.git.repository import current_revision, head_commit, is_dirty, open_repository
from rift.git.revisions import resolve_base, validate_revision
from rift.git.version import is_supported, require_supported_git
from tests.fixtures.git_repo import GitRepo

pytestmark = pytest.mark.integration


def test_installed_git_is_supported() -> None:
    assert is_supported(require_supported_git())


def test_open_repository_from_root_and_subdirectory(repo: GitRepo) -> None:
    repo.write("pkg/mod.py", "x = 1\n")
    repo.commit_all()
    for start in (repo.root, repo.root / "pkg"):
        opened = open_repository(start)
        assert opened.root.resolve() == repo.root.resolve()
        assert opened.is_shallow is False
        assert opened.default_branch is None  # no origin


def test_not_a_repository(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotARepositoryError) as exc_info:
        open_repository(plain)
    assert exc_info.value.code is ErrorCode.NOT_A_REPOSITORY
    assert exc_info.value.hint
    with pytest.raises(NotARepositoryError):
        open_repository(tmp_path / "does-not-exist")


def test_bare_repository_is_not_a_working_tree(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    bare.mkdir()
    GitRepo(bare).git("init", "-q", "--bare")
    with pytest.raises(NotARepositoryError):
        open_repository(bare)


def test_current_revision_branch_dirty_untracked_detached_unborn(repo: GitRepo) -> None:
    unborn = current_revision(open_repository(repo.root))
    assert (unborn.commit, unborn.ref, unborn.is_detached) == (None, "main", False)

    repo.write("a.py", "a = 1\n")
    first = repo.commit_all("first")
    opened = open_repository(repo.root)
    clean = current_revision(opened)
    assert (clean.commit, clean.ref, clean.is_detached, clean.is_dirty) == (
        first,
        "main",
        False,
        False,
    )
    assert head_commit(opened) == first

    repo.write("a.py", "a = 2\n")
    assert current_revision(opened).is_dirty is True
    repo.git("checkout", "-q", "--", "a.py")
    assert is_dirty(opened) is False

    repo.write("untracked.txt", "new\n")
    assert is_dirty(opened) is True  # untracked, non-ignored files count as dirty (ADR-0006)
    repo.write(".git/info/exclude", "untracked.txt\n")
    assert is_dirty(opened) is False  # ...but ignored ones do not

    repo.git("checkout", "-q", "--detach")
    detached = current_revision(opened)
    assert (detached.commit, detached.ref, detached.is_detached) == (first, None, True)


def test_validate_revision_forms(repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    first = repo.commit_all("first")
    repo.write("a.py", "2\n")
    second = repo.commit_all("second")
    repo.git("tag", "-a", "-m", "annotated", "v1", first)
    repo.git("tag", "light", second)
    opened = open_repository(repo.root)
    assert validate_revision(opened, "main") == second
    assert validate_revision(opened, "HEAD~1") == first
    assert validate_revision(opened, first[:10]) == first
    assert validate_revision(opened, "v1") == first  # annotated tag peeled to its commit
    assert validate_revision(opened, "light") == second
    for bad in ["nope", "HEAD^{tree}", "main..HEAD", "0" * 40]:
        with pytest.raises(InvalidRevisionError):
            validate_revision(opened, bad)


def test_resolve_base_precedence_and_merge_base(tmp_path: Path, repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    root_commit = repo.commit_all("root")
    clone = GitRepo(tmp_path / "clone")
    GitRepo(tmp_path).git("clone", "-q", repo.root.as_uri(), str(clone.root))
    clone.git("config", "core.autocrlf", "false")
    clone.write("b.py", "2\n")
    clone.commit_all("local work")
    opened = open_repository(clone.root)
    assert opened.default_branch == "main"
    assert resolve_base(opened) == root_commit  # merge-base HEAD origin/HEAD
    assert resolve_base(opened, default_base="HEAD") == clone.head()
    assert resolve_base(opened, explicit=root_commit, default_base="HEAD") == root_commit


def test_resolve_base_without_any_source_is_a_clear_error(repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    repo.commit_all()
    with pytest.raises(GitError) as exc_info:
        resolve_base(open_repository(repo.root))
    assert exc_info.value.code is ErrorCode.NO_BASE_REVISION
    assert "--base" in (exc_info.value.hint or "")


def test_shallow_clone_detection_and_unreachable_base(tmp_path: Path, repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    old = repo.commit_all("old")
    repo.write("a.py", "2\n")
    repo.commit_all("new")
    shallow = GitRepo(tmp_path / "shallow")
    # file:// is required for --depth to take effect on a local clone.
    GitRepo(tmp_path).git("clone", "-q", "--depth", "1", repo.root.as_uri(), str(shallow.root))
    opened = open_repository(shallow.root)
    assert opened.is_shallow is True
    assert validate_revision(opened, "HEAD") == shallow.head()
    with pytest.raises(ShallowHistoryError) as exc_info:
        validate_revision(opened, old)
    assert exc_info.value.code is ErrorCode.SHALLOW_HISTORY
    assert "fetch-depth: 0" in (exc_info.value.hint or "")
