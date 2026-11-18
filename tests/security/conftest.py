"""Security tests run real git, isolated from the developer's git configuration."""

from pathlib import Path

import pytest

from tests.fixtures.git_repo import GitRepo, hermetic_git_env


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    return hermetic_git_env(monkeypatch, tmp_path)


@pytest.fixture
def global_gitconfig(_hermetic_git: Path) -> Path:
    return _hermetic_git


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo.init(tmp_path / "repo")
