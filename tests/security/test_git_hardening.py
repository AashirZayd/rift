"""SPEC §21 controls for rift.git: no repository-controlled code runs, no option injection.

Canary tests run every public rift.git call FIRST and assert the canary never ran, THEN run plain,
unhardened git as a positive control and assert the canary did run. Ordering matters: running the
control first could refresh the index and make the negative check pass vacuously.
"""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from rift.core.errors import ErrorCode
from rift.git.changes import detect_changes
from rift.git.errors import GitError, InvalidRevisionError
from rift.git.repository import (
    current_revision,
    head_commit,
    is_dirty,
    open_repository,
    repository_config_overrides,
)
from rift.git.revisions import resolve_base, validate_revision
from tests.fixtures.git_repo import GitRepo, lines

pytestmark = pytest.mark.security


def _canary(directory: Path, name: str) -> tuple[Path, Path]:
    """A shell script that records each invocation in ``<name>.hit`` (git runs it via sh)."""
    marker = directory / f"{name}.hit"
    script = directory / f"{name}.sh"
    script.write_text(f'#!/bin/sh\necho hit >> "{marker.as_posix()}"\ncat\n', encoding="utf-8")
    script.chmod(0o755)
    return script, marker


def _shell_word(script: Path) -> str:
    """git runs these config values through a shell; quote the path (it may contain spaces)."""
    return '"' + script.as_posix() + '"'


def _every_rift_git_call(root: Path) -> None:
    repo = open_repository(root)
    current_revision(repo)
    is_dirty(repo)
    head_commit(repo)
    base = validate_revision(repo, "HEAD~1")
    resolve_base(repo, explicit="HEAD~1")
    repository_config_overrides(repo.root)
    detect_changes(repo, base=base)  # working tree
    detect_changes(repo, base=base, head="HEAD")  # commit to commit


def _plain_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=60, check=False)


def _repo_with_history(path: Path, attributes: str = "") -> GitRepo:
    repo = GitRepo.init(path)
    repo.write("app.py", lines(5))
    if attributes:
        repo.write(".gitattributes", attributes)
    repo.commit_all("one")
    repo.write("app.py", lines(6))
    repo.commit_all("two")
    repo.write("app.py", lines(7))  # dirty working tree: forces working-tree comparisons
    repo.write("untracked.py", "u\n")
    return repo


CANARIES: list[tuple[str, str, str, tuple[str, ...]]] = [
    # (name, config key, .gitattributes, plain-git command that runs it without hardening)
    ("fsmonitor", "core.fsmonitor", "", ("status",)),
    ("external", "diff.external", "", ("diff", "HEAD")),
    ("textconv", "diff.evil.textconv", "*.py diff=evil\n", ("diff", "HEAD")),
    ("filter", "filter.evil.clean", "*.py filter=evil\n", ("diff", "HEAD")),
]


@pytest.mark.parametrize(
    ("name", "key", "attributes", "control"), CANARIES, ids=[c[0] for c in CANARIES]
)
def test_repository_config_commands_never_run(
    tmp_path: Path, name: str, key: str, attributes: str, control: tuple[str, ...]
) -> None:
    repo = _repo_with_history(tmp_path / "repo", attributes)
    script, marker = _canary(tmp_path, name)
    repo.git("config", key, _shell_word(script))

    _every_rift_git_call(repo.root)
    assert not marker.exists(), f"rift.git executed the repository-configured {key}"

    _plain_git(repo.root, *control)
    assert marker.exists(), f"positive control: plain `git {' '.join(control)}` must run {key}"


def test_pager_config_never_runs(tmp_path: Path) -> None:
    # No positive control: git only pages when stdout is a terminal, which tests cannot provide.
    # This still pins that RIFT neither invokes a configured pager nor fails because of one.
    repo = _repo_with_history(tmp_path / "repo")
    script, marker = _canary(tmp_path, "pager")
    for key in ("core.pager", "pager.diff", "pager.status", "pager.log"):
        repo.git("config", key, _shell_word(script))
    _every_rift_git_call(repo.root)
    assert not marker.exists()


def test_filter_driver_name_that_cannot_be_neutralized_is_refused(tmp_path: Path) -> None:
    repo = _repo_with_history(tmp_path / "repo", "*.py filter=a=b\n")
    script, marker = _canary(tmp_path, "eqfilter")
    repo.git("config", "filter.a=b.clean", _shell_word(script))
    with pytest.raises(GitError) as exc_info:
        detect_changes(open_repository(repo.root), base="HEAD~1")
    assert exc_info.value.code is ErrorCode.UNSAFE_REPOSITORY
    assert not marker.exists()


def test_user_global_filters_are_trusted_and_left_alone(
    tmp_path: Path, global_gitconfig: Path
) -> None:
    repo = _repo_with_history(tmp_path / "repo")
    global_gitconfig.write_text('[filter "lfs"]\n clean = git-lfs clean -- %f\n', encoding="utf-8")
    repo.git("config", "filter.local-only.clean", "x")
    assert repository_config_overrides(repo.root) == (
        "filter.local-only.clean=",
        "filter.local-only.smudge=",
        "filter.local-only.process=",
        "filter.local-only.required=false",
    )


INJECTIONS = [
    "--output=PWNED",
    "-h",
    "--help",
    "-",
    "--all",
    "--end-of-options",
    "-c core.pager=touch PWNED",
    "HEAD\n--output=PWNED",
    "HEAD\x00--output=PWNED",
    "\rHEAD",
]
SHELL_LOOKALIKES = [
    ";rm -rf .",
    "HEAD; touch PWNED",
    "$(touch PWNED)",
    "`touch PWNED`",
    "HEAD | touch PWNED",
    "HEAD && touch PWNED",
]


def _revision_entry_points(repo_root: Path) -> list[Callable[[str], object]]:
    repo = open_repository(repo_root)
    return [
        lambda rev: validate_revision(repo, rev),
        lambda rev: resolve_base(repo, explicit=rev),
        lambda rev: resolve_base(repo, default_base=rev),
        lambda rev: detect_changes(repo, base=rev),
        lambda rev: detect_changes(repo, base="HEAD~1", head=rev),
    ]


@pytest.mark.parametrize("rev", INJECTIONS + SHELL_LOOKALIKES)
def test_revision_arguments_cannot_inject_options_or_commands(tmp_path: Path, rev: str) -> None:
    repo = _repo_with_history(tmp_path / "repo")
    before = sorted(p.name for p in repo.root.iterdir())
    for call in _revision_entry_points(repo.root):
        with pytest.raises(InvalidRevisionError) as exc_info:
            call(rev)
        assert exc_info.value.code is ErrorCode.INVALID_REVISION
    assert not (repo.root / "PWNED").exists()
    assert not (tmp_path / "PWNED").exists()
    assert sorted(p.name for p in repo.root.iterdir()) == before


def test_leading_dash_is_rejected_before_git_runs(tmp_path: Path) -> None:
    repo = open_repository(_repo_with_history(tmp_path / "repo").root)
    with pytest.raises(InvalidRevisionError, match="must not start with '-'"):
        validate_revision(repo, "--output=PWNED")


def test_non_utf8_filename_is_refused_not_guessed(tmp_path: Path) -> None:
    repo = GitRepo.init(tmp_path / "repo")
    repo.stage_blob("ok.py", b"1\n")
    base = repo.commit_index()
    repo.stage_blob(b"bad\xffname.py", b"2\n")
    head = repo.commit_index()
    with pytest.raises(GitError) as exc_info:
        detect_changes(open_repository(repo.root), base=base, head=head)
    assert exc_info.value.code is ErrorCode.UNDECODABLE_PATH


def test_planted_git_executable_in_repository_is_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_history(tmp_path / "repo")
    marker = tmp_path / "planted.hit"
    if os.name == "nt":
        planted = repo.root / "git.exe"
        planted.write_bytes(b"MZ not really an executable")  # running it would raise
    else:
        planted = repo.root / "git"
        planted.write_text(f'#!/bin/sh\necho hit > "{marker}"\n', encoding="utf-8")
        planted.chmod(0o755)
    monkeypatch.chdir(repo.root)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", os.environ["PATH"]]))
    _every_rift_git_call(repo.root)
    assert not marker.exists()


def test_partial_clone_never_fetches_or_runs_transport_commands(tmp_path: Path) -> None:
    """A blob:none partial clone lacks old blobs; plain git would lazily fetch them from the
    promisor remote (network + repository-configured transport commands). RIFT must refuse."""
    origin = GitRepo.init(tmp_path / "origin")
    origin.git("config", "uploadpack.allowFilter", "true")
    for i in range(3):
        origin.write("a.py", lines(3 + i))
        origin.commit_all(f"c{i}")
    clone = GitRepo(tmp_path / "partial")
    GitRepo(tmp_path).git(
        "clone", "-q", "--filter=blob:none", origin.root.as_uri(), str(clone.root)
    )
    script = tmp_path / "uploadpack.sh"
    marker = tmp_path / "uploadpack.hit"
    script.write_text(
        f'#!/bin/sh\necho hit >> "{marker.as_posix()}"\nexec git upload-pack "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    clone.git("config", "remote.origin.uploadpack", _shell_word(script))

    with pytest.raises(GitError) as exc_info:
        detect_changes(open_repository(clone.root), base="HEAD~2", head="HEAD~1")
    assert exc_info.value.code is ErrorCode.MISSING_OBJECTS
    assert exc_info.value.hint
    assert not marker.exists(), "rift.git contacted the promisor remote"

    _plain_git(clone.root, "diff", "HEAD~2", "HEAD~1")
    assert marker.exists(), "positive control: plain git must lazily fetch via uploadpack"


def test_submodule_filter_driver_never_runs(tmp_path: Path) -> None:
    """A filter defined in a submodule's own config is outside the superproject's config, so
    RIFT cannot neutralize it; it must never make git run inside the submodule at all."""
    upstream = GitRepo.init(tmp_path / "upstream")
    upstream.write(".gitattributes", "*.py filter=evil\n")
    upstream.write("lib.py", lines(3))
    upstream.commit_all("lib")
    repo = GitRepo.init(tmp_path / "super")
    repo.write("app.py", "1\n")
    repo.commit_all("app")
    repo.git(
        "-c", "protocol.file.allow=always",
        "submodule", "add", "-q", upstream.root.as_uri(), "sub",
    )  # fmt: skip
    repo.commit_all("add submodule")
    repo.write("app.py", "2\n")
    base = repo.git("rev-parse", "HEAD~1")
    script, marker = _canary(tmp_path, "subfilter")
    GitRepo(repo.root / "sub").git("config", "filter.evil.clean", _shell_word(script))
    # Same-size edit inside the submodule: git cannot tell it changed from the size alone, so it
    # must hash the content, which runs the clean filter.
    repo.write("sub/lib.py", lines(3).replace("line 1", "LINE 1"))

    opened = open_repository(repo.root)
    is_dirty(opened)
    current_revision(opened)
    detect_changes(opened, base=base)
    detect_changes(opened, base=base, head="HEAD")
    assert not marker.exists(), "rift.git ran a submodule's filter driver"

    _plain_git(repo.root, "status")
    assert marker.exists(), "positive control: plain `git status` must run the submodule filter"
