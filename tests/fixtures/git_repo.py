"""Builder for real git repositories in ``tmp_path`` (no committed .git dirs, no mocked git).

The builder drives the real ``git`` CLI directly (not ``rift.git``), so tests of ``rift.git`` never
depend on the code under test to set up their fixtures.
"""

import subprocess
from pathlib import Path

import pytest

FIXED_DATE = "2026-01-01T00:00:00+00:00"


def hermetic_git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate git from the developer's machine; returns the (empty) global config path.

    No system config, an empty global config, fixed identities and dates (deterministic SHAs), and a
    ceiling directory so repository discovery never walks above ``tmp_path``.
    """
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("", encoding="utf-8")
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_AUTHOR_NAME": "RIFT Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "RIFT Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_DATE": FIXED_DATE,
        "GIT_CEILING_DIRECTORIES": str(tmp_path.parent),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(key, raising=False)
    return global_config


class GitRepo:
    """A real repository with helpers for building fixture histories."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def init(cls, path: Path, *, branch: str = "main") -> "GitRepo":
        path.mkdir(parents=True, exist_ok=True)
        repo = cls(path)
        repo.git("init", "-q", "-b", branch)
        repo.git("config", "core.autocrlf", "false")
        # Lets index-only fixtures hold names NTFS forbids (newline, tab, quote, backslash, colon).
        # Such entries are only ever diffed commit-to-commit, never checked out.
        repo.git("config", "core.protectNTFS", "false")
        return repo

    def git(self, *args: str | bytes, stdin: bytes | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            input=stdin,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"git {args!r} failed ({completed.returncode}): "
                f"{completed.stderr.decode('utf-8', 'replace')}"
            )
        return completed.stdout.decode("utf-8", "replace").strip()

    def write(self, rel: str, content: str | bytes) -> Path:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
        return target

    def commit_all(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def stage_blob(self, path: bytes | str, content: bytes, *, mode: str = "100644") -> None:
        """Put ``path`` straight into the index (works for names the filesystem cannot hold)."""
        sha = self.git("hash-object", "-w", "--stdin", stdin=content)
        raw_path = path.encode("utf-8") if isinstance(path, str) else path
        if b"\n" in raw_path:  # --index-info is line-based; use -z records for such names
            record = f"{mode} {sha}\t".encode() + raw_path + b"\0"
            self.git("update-index", "-z", "--index-info", stdin=record)
        else:
            self.git(
                "update-index", "--index-info", stdin=f"{mode} {sha}\t".encode() + raw_path + b"\n"
            )

    def unstage(self, path: bytes | str) -> None:
        raw_path = path.encode("utf-8") if isinstance(path, str) else path
        record = b"0 " + b"0" * 40 + b"\t" + raw_path + b"\0"
        self.git("update-index", "-z", "--index-info", stdin=record)

    def commit_index(self, message: str = "commit") -> str:
        """Commit exactly what is in the index (no working-tree add)."""
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()


def lines(count: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {i}\n" for i in range(1, count + 1))
