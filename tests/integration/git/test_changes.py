"""Change detection against real repositories (SPEC §8, §24 M1)."""

import os
import sys
from pathlib import Path

import pytest

from rift.core.canonical import canonical_json
from rift.core.errors import ErrorCode
from rift.core.models.change import ChangedFile, ChangeSet, ChangeStatus, LineRange
from rift.core.serialization import to_dict
from rift.git.changes import detect_changes
from rift.git.errors import GitError, InvalidRevisionError, ShallowHistoryError
from rift.git.repository import open_repository
from rift.git.revisions import resolve_base
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
    attributes = global_gitconfig.with_name("hostile.gitattributes")
    attributes.write_text("*.py -diff binary\n", encoding="utf-8")  # user marks .py binary
    global_gitconfig.write_text(
        "[diff]\n algorithm = histogram\n noprefix = true\n relative = true\n renames = copies\n"
        " renameLimit = 1\n context = 10\n interHunkContext = 50\n mnemonicPrefix = true\n"
        " indentHeuristic = false\n ignoreSubmodules = all\n"
        "[color]\n ui = always\n diff = always\n"
        f"[core]\n quotePath = true\n pager = false\n attributesFile = {attributes.as_posix()}\n"
        "[status]\n showUntrackedFiles = no\n renames = false\n",
        encoding="utf-8",
    )
    hostile = (
        canonical_json(to_dict(detect_changes(opened, base=base, head=head))),
        canonical_json(to_dict(detect_changes(opened, base=base))),
    )
    assert hostile == clean


# --------------------------------------------------------------------------- review regressions


def test_untracked_nested_repository_is_one_added_entry(tmp_path: Path, repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    base = repo.commit_all()
    nested = GitRepo.init(repo.root / "vendor" / "lib")
    nested.write("inner.py", "x\n")
    nested.commit_all()
    files = by_path(detect_changes(open_repository(repo.root), base=base))
    assert files["vendor/lib"].status is ChangeStatus.ADDED
    assert files["vendor/lib"].changed_lines == ()
    assert "vendor/lib/inner.py" not in files


def test_rm_cached_and_recreated_files_become_one_modified_entry(repo: GitRepo) -> None:
    repo.write("cached.py", lines(3))
    repo.write("recreated.py", lines(2))
    base = repo.commit_all()
    repo.git("rm", "-q", "--cached", "cached.py")  # still on disk, now untracked
    repo.git("rm", "-q", "recreated.py")
    repo.write("recreated.py", lines(5, "new"))  # re-created, untracked
    files = by_path(detect_changes(open_repository(repo.root), base=base))
    assert files["cached.py"].status is ChangeStatus.MODIFIED
    assert files["cached.py"].changed_lines == ranges((1, 3))
    assert files["recreated.py"].status is ChangeStatus.MODIFIED
    assert files["recreated.py"].changed_lines == ranges((1, 5))


def test_staged_rename_in_working_tree(repo: GitRepo) -> None:
    repo.write("old_name.py", lines(10))
    base = repo.commit_all()
    repo.git("mv", "old_name.py", "new_name.py")
    repo.write("new_name.py", lines(10).replace("line 9\n", "nine\n"))
    files = by_path(detect_changes(open_repository(repo.root), base=base))
    assert (files["new_name.py"].status, files["new_name.py"].old_path) == (
        ChangeStatus.RENAMED,
        "old_name.py",
    )
    assert files["new_name.py"].changed_lines == ranges((9, 9))


WORKTREE_NAMES = ["with space.py", "-leading-dash.py", "--output=x.py", "ünïcødé/файл.py"]
if sys.platform != "win32":  # these characters cannot exist in a Windows file name
    WORKTREE_NAMES += ["new\nline.py", "tab\tname.py", r"back\slash.py", 'quote"name.py']


@pytest.mark.parametrize("name", WORKTREE_NAMES)
def test_exotic_names_modified_in_the_working_tree(repo: GitRepo, name: str) -> None:
    repo.write(name, lines(4))
    base = repo.commit_all()
    repo.write(name, lines(4).replace("line 3\n", "THREE\n"))  # unstaged: goes via pathspec
    files = by_path(detect_changes(open_repository(repo.root), base=base))
    assert files[name].status is ChangeStatus.MODIFIED
    assert files[name].changed_lines == ranges((3, 3))


def test_binary_is_decided_by_content_not_attributes(repo: GitRepo) -> None:
    repo.write(".gitattributes", "*.dat diff\n*.py -diff\n")
    repo.write("data.dat", b"head\x00binary\n")
    repo.write("code.py", lines(4))
    base = repo.commit_all()
    repo.write("data.dat", b"head\x00binary changed\n")
    repo.write("code.py", lines(4).replace("line 2\n", "TWO\n"))
    head = repo.commit_all()
    opened = open_repository(repo.root)
    for change_set in (
        detect_changes(opened, base=base, head=head),
        detect_changes(opened, base=base),
    ):
        files = by_path(change_set)
        assert files["data.dat"].is_binary is True  # "diff" attribute cannot make NUL data text
        assert files["code.py"].is_binary is False  # "-diff" cannot hide text changes
        assert files["code.py"].changed_lines == ranges((2, 2))


def test_shallow_clone_change_detection_and_unreachable_merge_base(
    tmp_path: Path, repo: GitRepo
) -> None:
    repo.write("a.py", lines(3))
    root_commit = repo.commit_all("root")
    repo.write("a.py", lines(4))
    repo.commit_all("main")
    repo.git("branch", "other", root_commit)
    repo.git("checkout", "-q", "other")
    repo.write("b.py", "b\n")
    repo.commit_all("other")
    repo.git("checkout", "-q", "main")

    shallow = GitRepo(tmp_path / "shallow")
    GitRepo(tmp_path).git("clone", "-q", "--depth", "1", repo.root.as_uri(), str(shallow.root))
    shallow.git("config", "core.autocrlf", "false")
    opened = open_repository(shallow.root)
    # Change detection works within the fetched history.
    shallow.write("a.py", lines(4).replace("line 1\n", "one\n"))
    assert by_path(detect_changes(opened, base="HEAD"))["a.py"].changed_lines == ranges((1, 1))
    assert detect_changes(opened, base="HEAD", head="HEAD").files == ()
    # Ancestry beyond the fetched depth is a shallow-history problem, a typo is not.
    with pytest.raises(ShallowHistoryError):
        detect_changes(opened, base="HEAD~1")
    with pytest.raises(InvalidRevisionError):
        detect_changes(opened, base="no-such-branch")
    # Diverged branch fetched at depth 1: the merge base is outside the shallow history.
    shallow.git("checkout", "-q", "--", "a.py")
    shallow.git("fetch", "-q", "--depth", "1", "origin", "other:refs/remotes/origin/other")
    shallow.git("checkout", "-q", "-b", "work", "origin/other")
    with pytest.raises(ShallowHistoryError, match="merge base"):
        resolve_base(open_repository(shallow.root))


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions (and not root, who can read anything)",
)
def test_unreadable_untracked_file_is_a_structured_error(repo: GitRepo) -> None:
    repo.write("a.py", "1\n")
    base = repo.commit_all()
    secret = repo.write("unreadable.py", "x\n")
    secret.chmod(0o000)
    try:
        with pytest.raises(GitError) as exc_info:
            detect_changes(open_repository(repo.root), base=base)
        assert exc_info.value.code is ErrorCode.UNREADABLE_FILE
    finally:
        secret.chmod(0o644)
