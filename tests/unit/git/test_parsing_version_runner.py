"""Pure git output parsers, version policy, and run_git argv construction."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rift.core.errors import ErrorCode
from rift.core.models.change import ChangeStatus, LineRange
from rift.git.errors import GitError, GitUnavailableError
from rift.git.parsing import (
    count_lines,
    decode_path,
    parse_filter_drivers,
    parse_hunk_ranges,
    parse_numstat_z,
    parse_paths_z,
    parse_raw_z,
)
from rift.git.runner import HARDENING_ARGS, config_overrides, locate_git, run_git
from rift.git.version import MIN_GIT_VERSION, GitVersion, is_supported, parse_git_version

A = "a" * 40
B = "b" * 40
Z = "0" * 40


def raw(old_mode: str, new_mode: str, old: str, new: str, status: str, *paths: bytes) -> bytes:
    header = f":{old_mode} {new_mode} {old} {new} {status}".encode()
    return b"".join([header, b"\x00", *(p + b"\x00" for p in paths)])


def test_raw_records_with_exotic_names_renames_and_zero_shas() -> None:
    data = b"".join(
        [
            raw("100644", "100644", A, B, "M", b"-leading-dash.py"),
            raw("000000", "100644", Z, B, "A", b"new\nline.py"),
            raw("100644", "100644", A, B, "R087", b"old name.py", b"new\tname.py"),
            raw("100644", "100644", A, A, "C100", b"src.py", b"copy.py"),
            raw("100644", "000000", A, Z, "D", b"\xc3\xbcn\xc3\xafc\xc3\xb8d\xc3\xa9.py"),
            raw("100644", "120000", A, B, "T", b"link"),
            raw("100644", "100644", A, Z, "M", b"worktree.py"),
            raw("160000", "160000", A, B, "M", b"vendor/sub"),
            raw("000000", "100644", Z, Z, "U", b"conflicted.py"),
        ]
    )
    changes = parse_raw_z(data)
    assert [(c.status, c.path, c.old_path, c.old_sha, c.new_sha) for c in changes] == [
        (ChangeStatus.MODIFIED, "-leading-dash.py", None, A, B),
        (ChangeStatus.ADDED, "new\nline.py", None, None, B),
        (ChangeStatus.RENAMED, "new\tname.py", "old name.py", A, B),
        (ChangeStatus.COPIED, "copy.py", "src.py", A, A),
        (ChangeStatus.DELETED, "ünïcødé.py", None, A, None),
        (ChangeStatus.TYPE_CHANGED, "link", None, A, B),
        (ChangeStatus.MODIFIED, "worktree.py", None, A, None),
        (ChangeStatus.MODIFIED, "vendor/sub", None, A, B),
        (ChangeStatus.MODIFIED, "conflicted.py", None, None, None),
    ]
    assert [c.is_gitlink for c in changes] == [False] * 7 + [True, False]
    sha256 = "c" * 64
    assert parse_raw_z(raw("100644", "100644", sha256, sha256, "M", b"x"))[0].old_sha == sha256


@pytest.mark.parametrize(
    "data",
    [
        raw("100644", "100644", A, B, "M", b"a.py")[:-1],
        raw("100644", "100644", A, B, "X", b"a.py"),
        raw("100644", "100644", A, B, "R100", b"only-old"),
        raw("100644", "100644", A, B, "M"),
        b"M\x00a.py\x00",
        raw("100644", "100644", A[:-1], B, "M", b"a.py"),
    ],
    ids=["no-trailing-nul", "unknown-X", "truncated-rename", "truncated", "not-raw", "short-sha"],
)
def test_raw_rejects_malformed_output(data: bytes) -> None:
    with pytest.raises(GitError) as exc_info:
        parse_raw_z(data)
    assert exc_info.value.code is ErrorCode.UNEXPECTED_GIT_OUTPUT


def test_count_lines() -> None:
    assert [count_lines(d) for d in (b"", b"a", b"a\n", b"a\nb", b"\n\n")] == [0, 1, 1, 2, 2]


def test_numstat_binary_text_and_renames() -> None:
    # Explicit join with \x00: adjacent literals ending in \0 and starting with a digit would merge
    # into an octal escape (b"\0" b"0" -> b"\00") if a formatter joined them.
    data = b"".join(
        [
            b"3\t1\ttext.py\x00",
            b"-\t-\timage.png\x00",
            b"0\t0\t\x00old.py\x00new.py\x00",
            b"2\t2\ttab\there.py\x00",
        ]
    )
    assert parse_numstat_z(data) == {
        "text.py": False,
        "image.png": True,
        "new.py": False,
        "tab\there.py": False,
    }


def test_hunk_ranges_new_side_old_side_and_pure_deletions() -> None:
    patch = b"\n".join(
        [
            b"diff --git a/f b/f",
            b"@@ -3 +3 @@ ctx",
            b"-old",
            b"+new",
            b"@@ -10,0 +11,2 @@",
            b"+a",
            b"+b",
            b"@@ -20,3 +21,0 @@",  # pure deletion: anchored at new line 21
            b"@@ -0,0 +1 @@",
            b"+@@ -99 +99 @@ not a header because of the leading +",
        ]
    )
    assert parse_hunk_ranges(patch, old_side=False) == (
        LineRange(start=1, end=1),
        LineRange(start=3, end=3),
        LineRange(start=11, end=12),
        LineRange(start=21, end=21),
    )
    assert parse_hunk_ranges(b"@@ -1,4 +0,0 @@\n", old_side=True) == (LineRange(start=1, end=4),)
    assert parse_hunk_ranges(b"@@ -5,1 +4,0 @@\n", old_side=False) == (LineRange(start=4, end=4),)
    assert parse_hunk_ranges(b"@@ -1,1 +0,0 @@\n", old_side=False) == (LineRange(start=1, end=1),)
    assert parse_hunk_ranges(b"Binary files differ\n", old_side=False) == ()


def test_paths_and_decode_path() -> None:
    assert parse_paths_z(b"a b.py\0dir/c.py\0") == ["a b.py", "dir/c.py"]
    assert parse_paths_z(b"") == []
    with pytest.raises(GitError) as exc_info:
        decode_path(b"bad\xffname.py")
    assert exc_info.value.code is ErrorCode.UNDECODABLE_PATH
    with pytest.raises(GitError):
        decode_path(b"/abs/path")


def test_filter_driver_scopes() -> None:
    data = (
        b"global\0filter.lfs.clean\ngit-lfs clean -- %f\0"
        b"local\0filter.Ev.il.clean\nevil\0"
        b"local\0filter.Ev.il.required\ntrue\0"
        b"worktree\0filter.wt.process\nx\0"
        b"local\0filter.noname\nx\0"
    )
    assert parse_filter_drivers(data, frozenset({"local", "worktree"})) == ["Ev.il", "wt"]
    assert parse_filter_drivers(b"", frozenset({"local"})) == []
    with pytest.raises(GitError):
        parse_filter_drivers(b"local\0", frozenset({"local"}))


@pytest.mark.parametrize(
    ("output", "triple"),
    [
        ("git version 2.56.1.windows.1\n", (2, 56, 1)),
        ("git version 2.39.3 (Apple Git-146)\n", (2, 39, 3)),
        ("git version 2.30.2\n", (2, 30, 2)),
        ("git version 3.0\n", (3, 0, 0)),
    ],
)
def test_parse_git_version(output: str, triple: tuple[int, int, int]) -> None:
    assert parse_git_version(output).triple == triple


def test_parse_git_version_rejects_garbage() -> None:
    with pytest.raises(GitUnavailableError):
        parse_git_version("not git at all")


def test_minimum_version_policy() -> None:
    assert MIN_GIT_VERSION == (2, 30, 0)
    assert is_supported(GitVersion(major=2, minor=30, patch=0))
    assert is_supported(GitVersion(major=3, minor=0, patch=0))
    assert not is_supported(GitVersion(major=2, minor=29, patch=9))
    assert not is_supported(GitVersion(major=1, minor=99, patch=99))


def test_hardening_args_are_exactly_spec_section_8() -> None:
    assert HARDENING_ARGS == (
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "-c", "diff.external=",
        "-c", "core.pager=cat",
        "-c", "color.ui=false",
    )  # fmt: skip


def test_config_overrides() -> None:
    assert config_overrides(["a.b=", "c.d=1"]) == ("-c", "a.b=", "-c", "c.d=1")
    for bad in ["no-equals", "--upload-pack=evil"]:
        with pytest.raises(GitError):
            config_overrides([bad])


def test_locate_git_ignores_relative_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "git.exe" if os.name == "nt" else "git"
    real = locate_git()
    for directory in (tmp_path, tmp_path / "planted"):
        directory.mkdir(exist_ok=True)
        fake = directory / name
        fake.write_bytes(b"")
        fake.chmod(0o755)
    monkeypatch.chdir(tmp_path)  # "", "." and "planted" now all resolve to a planted git
    search = os.pathsep.join(["", ".", "planted", str(real.parent)])
    assert locate_git(search) == real


def test_locate_git_never_searches_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "git.exe" if os.name == "nt" else "git"
    (tmp_path / name).write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    found = locate_git()
    assert found.is_absolute()
    assert found.parent != tmp_path


def test_locate_git_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(GitUnavailableError):
        locate_git(str(tmp_path))


def test_run_git_argv_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unit test with a mocked subprocess.run (labelled per CLAUDE.md): checks argv and kwargs."""
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["argv"], captured["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, b"out", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)  # runner calls subprocess.run at call time
    result = run_git(["status"], cwd=tmp_path, extra_config=["filter.x.clean="])
    argv = captured["argv"]
    assert Path(argv[0]).is_absolute()
    assert argv[1] == "--no-pager"
    assert tuple(argv[2 : 2 + len(HARDENING_ARGS)]) == HARDENING_ARGS
    assert argv[-3:] == ["-c", "filter.x.clean=", "status"]
    kwargs = captured["kwargs"]
    assert "shell" not in kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert kwargs["env"]["LC_ALL"] == "C"
    assert kwargs["env"]["GIT_PAGER"] == "cat"
    assert result.stdout == b"out"


def test_run_git_timeout_is_structured(tmp_path: Path) -> None:
    # A real git process with a timeout no process can meet.
    with pytest.raises(GitError) as exc_info:
        run_git(["--version"], cwd=tmp_path, timeout=1e-9)
    assert exc_info.value.code is ErrorCode.GIT_TIMEOUT


def test_run_git_failure_is_structured(tmp_path: Path) -> None:
    with pytest.raises(GitError) as exc_info:
        run_git(["this-is-not-a-git-command"], cwd=tmp_path)
    assert exc_info.value.code is ErrorCode.GIT_FAILED
    assert exc_info.value.stderr_tail
    assert dict(exc_info.value.context)["exit_code"] != "0"


def test_no_shell_true_anywhere_in_src() -> None:
    src = Path(__file__).resolve().parents[3] / "src"
    offenders = [str(p) for p in src.rglob("*.py") if "shell=True" in p.read_text(encoding="utf-8")]
    assert offenders == []
