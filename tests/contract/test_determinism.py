"""Determinism contract (SPEC §7.6): identical bytes across processes with different hash seeds.

M1 scope: canonical JSON and content IDs of core models, and real-git ChangeSets. M6 extends this
file to whole Change Proofs.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.git_repo import GitRepo, hermetic_git_env, lines

PROBE = Path(__file__).with_name("determinism_probe.py")
SEEDS = ("0", "4242")


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[GitRepo, str]:
    hermetic_git_env(monkeypatch, tmp_path)
    repo = GitRepo.init(tmp_path / "repo")
    repo.write("pkg/a.py", lines(12))
    repo.write("pkg/b.py", lines(8, "bee"))
    repo.write("docs/readme.md", "hello\n")
    base = repo.commit_all("base")
    repo.write("pkg/a.py", lines(12).replace("line 4\n", "LINE 4\n"))
    repo.git("mv", "pkg/b.py", "pkg/bee.py")
    repo.write("with space/ünï.py", "x\n")
    repo.commit_all("head")
    repo.write("pkg/a.py", lines(13))
    repo.write("untracked.py", "u\n")
    return repo, base


def _probe(repo: GitRepo, base: str, seed: str) -> dict[str, list[str]]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, str(PROBE), str(repo.root), base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result: dict[str, list[str]] = json.loads(proc.stdout)
    return result


@pytest.mark.integration
def test_outputs_are_byte_identical_across_hash_seeds(history: tuple[GitRepo, str]) -> None:
    repo, base = history
    first, second = (_probe(repo, base, seed) for seed in SEEDS)
    # Positive control: the seeds really do change Python's set iteration order...
    assert first["set_order"] != second["set_order"]
    # ...yet every serialized artifact and content ID is byte-identical.
    assert first["output"] == second["output"]
    assert len(first["output"]) == 7


@pytest.mark.integration
def test_change_sets_are_identical_across_runs_and_clones(
    history: tuple[GitRepo, str], tmp_path: Path
) -> None:
    repo, base = history
    clone = GitRepo(tmp_path / "clone")
    GitRepo(tmp_path).git("clone", "-q", repo.root.as_uri(), str(clone.root))
    original = _probe(repo, base, "1")["output"]
    cloned = _probe(clone, base, "2")["output"]
    assert cloned[0] == original[0]  # commit-to-commit ChangeSet: independent of checkout location
