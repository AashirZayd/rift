"""File enumeration, exclusions, link policy and source-root detection (ADR-0008)."""

import importlib
import os
import sys
from pathlib import Path

import pytest

from rift.analysis.python.analyzer import analyze_project
from rift.discovery.files import DiscoveryError, discover_python_files, matches_exclude
from rift.discovery.source_roots import detect_source_roots, importable_name, validate_source_root


def _touch(root: Path, *paths: str) -> None:
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")


def test_enumerates_sorted_python_files_and_skips_noise(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "b.py",
        "a.py",
        "pkg/__init__.py",
        "pkg/z.py",
        "pkg/notes.txt",
        "pkg/stub.pyi",
        ".git/hooks/x.py",
        ".venv/lib/x.py",
        "pkg/__pycache__/x.py",
        "node_modules/x.py",
        "thing.egg-info/x.py",
        "env/pyvenv.cfg",
        "env/lib/x.py",
        "envpkg/x.py",  # a real package that merely looks like an env
    )
    found = discover_python_files(tmp_path)
    assert found.files == ("a.py", "b.py", "envpkg/x.py", "pkg/__init__.py", "pkg/z.py")
    assert found.limitations == ()


def test_user_excludes(tmp_path: Path) -> None:
    _touch(tmp_path, "migrations/0001.py", "app/migrations/0002.py", "app/models.py", "build/x.py")
    found = discover_python_files(tmp_path, exclude=("**/migrations/**", "build/**"))
    assert found.files == ("app/models.py",)


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("a/migrations/x.py", "**/migrations/**", True),
        ("migrations/x.py", "**/migrations/**", True),
        ("a/b.py", "*.py", True),  # fnmatch: * crosses /
        ("a/b.py", "c/*", False),
    ],
)
def test_exclude_matching(path: str, pattern: str, expected: bool) -> None:
    assert matches_exclude(path, [pattern]) is expected


def _symlink_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:  # Windows without developer mode / privilege
        pytest.skip(f"cannot create symlinks here: {exc}")


@pytest.mark.parametrize("directory", [True, False])
def test_symlinks_are_never_followed(tmp_path: Path, directory: bool) -> None:
    outside = tmp_path / "outside"
    _touch(outside, "secret.py")
    root = tmp_path / "repo"
    _touch(root, "real.py")
    target = outside if directory else outside / "secret.py"
    name = "linked" if directory else "linked.py"
    _symlink_or_skip(root / name, target, directory=directory)
    found = discover_python_files(root)
    assert found.files == ("real.py",)
    assert [(lim.kind, lim.subject) for lim in found.limitations] == [("symlink_skipped", name)]
    # And the analyzer never reads through it.
    project = analyze_project(root)
    assert [r.file_path for r in project.results] == ["real.py"]
    assert [lim.kind for lim in project.limitations] == ["symlink_skipped"]


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_windows_junctions_are_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _touch(outside, "secret.py")
    root = tmp_path / "repo"
    root.mkdir()
    # Looked up dynamically: _winapi only exists (and only type-checks) on Windows.
    create_junction = vars(importlib.import_module("_winapi"))["CreateJunction"]
    create_junction(str(outside), str(root / "junction"))
    found = discover_python_files(root)
    assert found.files == ()
    assert [(lim.kind, lim.subject) for lim in found.limitations] == [
        ("symlink_skipped", "junction")
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_unreadable_directories_are_limitations(tmp_path: Path) -> None:
    _touch(tmp_path, "ok.py", "locked/x.py")
    locked = tmp_path / "locked"
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore permissions")
        found = discover_python_files(tmp_path)
    finally:
        locked.chmod(0o755)
    assert found.files == ("ok.py",)
    assert [(lim.kind, lim.subject, lim.message) for lim in found.limitations] == [
        ("unreadable_path", "locked", "skipped: EACCES")
    ]


def test_root_must_be_an_absolute_directory(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError):
        discover_python_files(Path("relative"))
    with pytest.raises(DiscoveryError):
        discover_python_files(tmp_path / "missing")


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["src/pkg/__init__.py"], ("src",)),
        (["src/ns/sub/mod.py"], ("src",)),  # namespace package
        (["src/single.py"], ("src",)),  # single-module src layout
        (["src/not-importable/x.py", "app/__init__.py"], (".",)),
        (["pkg/__init__.py"], (".",)),
        ([], (".",)),
    ],
)
def test_source_root_detection(files: list[str], expected: tuple[str, ...]) -> None:
    assert detect_source_roots(files) == expected


def test_importable_name_and_root_validation() -> None:
    assert importable_name("pkg/sub/__init__.py") == "pkg.sub"
    assert importable_name("pkg/mod.py") == "pkg.mod"
    assert importable_name("pkg/mod.txt") is None
    assert validate_source_root(".") == "."
    assert validate_source_root("lib/src") == "lib/src"
    for bad in ("", "/abs", "../up", "a//b"):
        with pytest.raises(DiscoveryError):
            validate_source_root(bad)
