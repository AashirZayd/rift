"""Change detection against real repositories (SPEC §8, §24 M1)."""

import sys
from pathlib import Path

import pytest

from rift.core.canonical import canonical_json
from rift.core.models.change import ChangedFile, ChangeSet, ChangeStatus, LineRange
from rift.core.serialization import to_dict
from rift.git.changes import detect_changes
from rift.git.repository import open_repository
from tests.fixtures.git_repo import GitRepo, lines

pytestmark = pytest.mark.integration


def by_path(change_set: ChangeSet) -> dict[str, ChangedFile]:
    return {changed.path: changed for changed in change_set.files}


def ranges(*pairs: tuple[int, int]) -> tuple[LineRange, ...]:
    return tuple(LineRange(start=s, end=e) for s, e in pairs)


def test_commit_to_commit_statuses_and_line_ranges(repo: GitRepo) -> None:
    repo.write("mod.py", lines(10))
    repo.write("gone.py", lines(4, "unrelated content of a file that will be deleted"))
    repo.write("moved.py", lines(20, "keep"))
    repo.write("moved_edit.py", lines(20, "edit"))
    repo.write("empty_later.py", "x\n")
    base = repo.commit_all("base")

    repo.write(
        "mod.py", lines(10).replace("line 3\n", "LINE 3\n").replace("line 7\n", "") + "line 11\n"
    )
    (repo.root / "gone.py").unlink()
    repo.write("added.py", lines(5))
    repo.write("empty.py", "")
    repo.git("mv", "moved.py", "renamed.py")
    repo.git("mv", "moved_edit.py", "renamed_edit.py")
    repo.write("renamed_edit.py", lines(20, "edit").replace("edit 2\n", "edit two\n"))
    repo.write("image.bin", b"\x00\x01\x02binary\x00")
    head = repo.commit_all("head")

    change_set = detect_changes(open_repository(repo.root), base=base, head=head)
    files = by_path(change_set)
    assert sorted(files) == [
        "added.py",
        "empty.py",
        "gone.py",
        "image.bin",
        "mod.py",
        "renamed.py",
        "renamed_edit.py",
    ]
    assert files["mod.py"].status is ChangeStatus.MODIFIED
    # line 3 replaced; old line 7 deleted (anchored at new line 6); line 11 appended (new 10)
    assert files["mod.py"].changed_lines == ranges((3, 3), (6, 6), (10, 10))
    assert files["gone.py"].status is ChangeStatus.DELETED
    assert files["gone.py"].changed_lines == ranges((1, 4))  # old-file lines for deletions
    assert files["added.py"].changed_lines == ranges((1, 5))
    assert files["empty.py"].changed_lines == ()
    assert (files["renamed.py"].status, files["renamed.py"].old_path) == (
        ChangeStatus.RENAMED,
        "moved.py",
    )
    assert files["renamed.py"].changed_lines == ()  # pure rename: no content change
    assert files["renamed_edit.py"].old_path == "moved_edit.py"
    assert files["renamed_edit.py"].changed_lines == ranges((2, 2))
    assert files["image.bin"].is_binary is True
    assert files["image.bin"].changed_lines == ()
    assert change_set.includes_uncommitted is False
    assert (change_set.base.commit, change_set.head.commit) == (base, head)


def test_rename_similarity_threshold(repo: GitRepo) -> None:
    repo.write("a.py", lines(10))
    base = repo.commit_all()
    repo.git("mv", "a.py", "b.py")
    repo.write("b.py", lines(10).replace("line 1\n", "changed\n"))
    head = repo.commit_all()
    opened = open_repository(repo.root)
    loose = by_path(detect_changes(opened, base=base, head=head, rename_similarity=50))
    assert loose["b.py"].status is ChangeStatus.RENAMED
    strict = by_path(detect_changes(opened, base=base, head=head, rename_similarity=100))
    assert (strict["a.py"].status, strict["b.py"].status) == (
        ChangeStatus.DELETED,
        ChangeStatus.ADDED,
    )


def test_type_change_file_to_symlink(repo: GitRepo) -> None:
    repo.stage_blob("target.py", b"x = 1\n")
    repo.stage_blob("entry", b"plain file\n")
    base = repo.commit_index()
    repo.stage_blob("entry", b"target.py", mode="120000")  # index-only: works without symlinks
    head = repo.commit_index()
    files = by_path(detect_changes(open_repository(repo.root), base=base, head=head))
    assert files["entry"].status is ChangeStatus.TYPE_CHANGED


EXOTIC_NAMES = [
    "with space.py",
    "ünïcødé/файл.py",
    "new\nline.py",
    "-leading-dash.py",
    "--output=x.py",
    "tab\tname.py",
    'quote"name.py',
    "back\\slash.py",
    "colon:name.py",
    "*star?.py",
]


def test_exotic_filenames_commit_to_commit(repo: GitRepo) -> None:
    repo.stage_blob("README", b"base\n")
    base = repo.commit_index("base")
    for name in EXOTIC_NAMES:
        repo.stage_blob(name, b"one\ntwo\n")
    head = repo.commit_index("exotic")
    files = by_path(detect_changes(open_repository(repo.root), base=base, head=head))
    assert sorted(files) == sorted(EXOTIC_NAMES)
    for name in EXOTIC_NAMES:
        assert files[name].status is ChangeStatus.ADDED, name
        assert files[name].changed_lines == ranges((1, 2)), name

    # Modify and rename exotic names too: line ranges must come from the exact file.
    for name in EXOTIC_NAMES:
        repo.stage_blob(name, b"one\nTWO\n")
    repo.unstage("with space.py")
    repo.stage_blob("renamed with space.py", b"one\ntwo\n")
    third = repo.commit_index("edit")
    files = by_path(detect_changes(open_repository(repo.root), base=head, head=third))
    assert files["renamed with space.py"].old_path == "with space.py"
    for name in EXOTIC_NAMES[1:]:
        assert files[name].status is ChangeStatus.MODIFIED, name
        assert files[name].changed_lines == ranges((2, 2)), name


def test_working_tree_staged_unstaged_untracked_and_ignored(repo: GitRepo) -> None:
    repo.write(".gitignore", "*.log\nbuild/\n")
    repo.write("tracked.py", lines(5))
    repo.write("staged.py", lines(3))
    base = repo.commit_all("base")

    repo.write("tracked.py", lines(5).replace("line 2\n", "LINE 2\n"))  # unstaged edit
    repo.write("staged.py", lines(3) + "line 4\n")
    repo.git("add", "staged.py")  # staged edit
    repo.write("new file.py", lines(7))  # untracked, with a space
    repo.write("-dash.py", "a\nb")  # untracked, leading dash, no trailing newline
    repo.write("ünï/cødé.py", "x\n")  # untracked, unicode directory
    repo.write("blob.dat", b"\x00\xff\x00")  # untracked binary
    repo.write("empty.txt", "")  # untracked, empty
    repo.write("debug.log", "ignored\n")
    repo.write("build/out.py", "ignored\n")

    opened = open_repository(repo.root)
    change_set = detect_changes(opened, base=base)
    files = by_path(change_set)
    assert sorted(files) == [
        "-dash.py",
        "blob.dat",
        "empty.txt",
        "new file.py",
        "staged.py",
        "tracked.py",
        "ünï/cødé.py",
    ]
    assert files["tracked.py"].changed_lines == ranges((2, 2))
    assert files["staged.py"].changed_lines == ranges((4, 4))
    assert files["new file.py"].status is ChangeStatus.ADDED
    assert files["new file.py"].changed_lines == ranges((1, 7))
    assert files["-dash.py"].changed_lines == ranges((1, 2))
    assert files["blob.dat"].is_binary is True
    assert files["empty.txt"].changed_lines == ()
    assert change_set.includes_uncommitted is True
    assert change_set.head.commit is None
    assert change_set.head.is_dirty is True
    assert change_set.head.ref == "main"


def test_working_tree_deletion_and_detached_head(repo: GitRepo) -> None:
    repo.write("keep.py", "1\n")
    repo.write("delete_me.py", lines(3))
    base = repo.commit_all()
    repo.git("checkout", "-q", "--detach")
    (repo.root / "delete_me.py").unlink()
    change_set = detect_changes(open_repository(repo.root), base=base)
    files = by_path(change_set)
    assert files["delete_me.py"].status is ChangeStatus.DELETED
    assert files["delete_me.py"].changed_lines == ranges((1, 3))
    assert (change_set.head.is_detached, change_set.head.ref) == (True, None)


def test_clean_working_tree_has_no_changes(repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    base = repo.commit_all()
    change_set = detect_changes(open_repository(repo.root), base=base)
    assert change_set.files == ()
    assert change_set.head.is_dirty is False


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privileges on Windows")
def test_untracked_symlink_is_not_followed(tmp_path: Path, repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    base = repo.commit_all()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n" * 100, encoding="utf-8")
    (repo.root / "link.txt").symlink_to(outside)
    files = by_path(detect_changes(open_repository(repo.root), base=base))
    assert files["link.txt"].changed_lines == ranges((1, 1))  # the link itself, not its target


def test_hostile_user_git_config_does_not_change_results(
    repo: GitRepo, global_gitconfig: Path
) -> None:
    repo.write("a.py", lines(30))
    for i in range(5):
        repo.write(f"r{i}.py", lines(15, f"file{i}"))
    base = repo.commit_all()
    repo.write("a.py", lines(30).replace("line 5\n", "x\n").replace("line 20\n", "y\n"))
    for i in range(5):
        repo.git("mv", f"r{i}.py", f"moved{i}.py")
    head = repo.commit_all()
    repo.write("a.py", lines(30).replace("line 25\n", "z\n"))
    repo.write("untracked.py", "u\n")

    opened = open_repository(repo.root)
    clean = (
        canonical_json(to_dict(detect_changes(opened, base=base, head=head))),
        canonical_json(to_dict(detect_changes(opened, base=base))),
    )
    global_gitconfig.write_text(
        "[diff]\n algorithm = histogram\n noprefix = true\n relative = true\n renames = copies\n"
        " renameLimit = 1\n context = 10\n interHunkContext = 50\n mnemonicPrefix = true\n"
        " indentHeuristic = false\n ignoreSubmodules = all\n"
        "[color]\n ui = always\n diff = always\n"
        "[core]\n quotePath = true\n pager = false\n"
        "[status]\n showUntrackedFiles = no\n renames = false\n",
        encoding="utf-8",
    )
    hostile = (
        canonical_json(to_dict(detect_changes(opened, base=base, head=head))),
        canonical_json(to_dict(detect_changes(opened, base=base))),
    )
    assert hostile == clean
