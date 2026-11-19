"""Determinism contract (SPEC §7.6): identical bytes across processes with different hash seeds.

M1 scope: canonical JSON and content IDs of core models, and real-git ChangeSets. M2 adds Python
AnalysisResults; M3 the dependency graph and its traversal output; M4 the ImpactResult of a real
change. M6 extends this file to whole
Change Proofs.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rift.analysis.python.analyzer import analyze_project
from rift.core.models.analysis import AnalysisResult
from rift.core.serialization import from_dict, to_dict
from rift.graph.multigraph import DependencyGraph
from tests.fixtures.git_repo import GitRepo, hermetic_git_env, lines
from tests.fixtures.python_project import write_project

PROBE = Path(__file__).with_name("determinism_probe.py")
ANALYSIS_PROBE = Path(__file__).with_name("analysis_probe.py")
GRAPH_PROBE = Path(__file__).with_name("graph_probe.py")
IMPACT_PROBE = Path(__file__).with_name("impact_probe.py")
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
    return _run([str(PROBE), str(repo.root), base], seed)


def _run(args: list[str], seed: str) -> dict[str, list[str]]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(
        [sys.executable, *args],
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


# A project exercising every analyzer feature whose output could depend on set or dict order:
# many names per scope, star imports, name-matched methods, re-exports, dynamic behavior.
_CORE = (
    "__all__ = ["
    + ", ".join(f"'f{i}'" for i in range(40))
    + "]\n"
    + "".join(f"def f{i}():\n    return f{(i + 1) % 40}()\n" for i in range(40))
)
ANALYSIS_PROJECT: dict[str, str | bytes] = {
    "src/app/__init__.py": "from app.core import *\nfrom .models import Model as Model\n",
    "src/app/core.py": _CORE,
    "src/app/models.py": """
        import importlib, sys
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from app import core
        try:
            import orjson as json
        except ImportError:
            import json
        class Base:
            def save(self): return self.validate()
            def validate(self): return True
        class Model(Base):
            def save(self):
                super().save()
                for name in ("a", "b", "c"):
                    getattr(self, name)
                importlib.import_module("app.core")
                sys.path.append("x")
                return eval("1")
        REGISTRY = [Base, Model]
    """,
    "src/ns/plugin/impl.py": "from app import *\ndef run(obj):\n    obj.save()\n    f1()\n",
    "tests/test_models.py": "from app import Model\ndef test_save():\n    Model().save()\n",
    "broken.py": "def f(:\n",
    "big.py": b"x = 1\n" * 200_000,  # > 1 MiB default max_file_bytes: oversized path
    "with space/ünï.py": "import app\n",
}


@pytest.mark.integration
def test_analysis_results_are_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", ANALYSIS_PROJECT)
    # Second seed on a copy at a different absolute path: catches absolute-path leaks too.
    copy = write_project(tmp_path / "elsewhere" / "copy", ANALYSIS_PROJECT)
    first = _run([str(ANALYSIS_PROBE), str(root)], SEEDS[0])
    second = _run([str(ANALYSIS_PROBE), str(copy)], SEEDS[1])
    assert first["set_order"] != second["set_order"]  # positive control
    assert first["output"] == second["output"]
    assert len(first["output"]) == len(ANALYSIS_PROJECT) + 2


def test_analysis_results_round_trip_and_are_stable_in_process(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", ANALYSIS_PROJECT)
    first = analyze_project(root)
    second = analyze_project(root)
    assert first == second
    kinds: set[str] = set()
    for result in first.results:
        data = to_dict(result)
        assert from_dict(AnalysisResult, data) == result
        kinds |= {e.kind.value for e in result.edges}
    # The corpus really exercises the interesting paths.
    assert kinds == {"imports", "calls", "inherits", "references"}
    reasons = {u.reason.value for r in first.results for u in r.unresolved}
    assert {"syntax_error", "exec_eval", "dynamic_attribute", "sys_path_mutation"} <= reasons


@pytest.mark.integration
def test_graph_and_traversal_are_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    root = write_project(tmp_path / "project", ANALYSIS_PROJECT)
    copy = write_project(tmp_path / "elsewhere" / "copy", ANALYSIS_PROJECT)
    first = _run([str(GRAPH_PROBE), str(root)], SEEDS[0])
    second = _run([str(GRAPH_PROBE), str(copy)], SEEDS[1])
    assert first["set_order"] != second["set_order"]  # positive control
    assert first["output"] == second["output"]
    graph = DependencyGraph.from_dict(json.loads(first["output"][0]))
    # Non-trivial corpus: many nodes, every analyzer edge kind, and real explanation paths.
    assert graph.node_count > 50
    assert {e.kind.value for e in graph.edges()} == {"imports", "calls", "inherits", "references"}
    assert len(first["output"]) == 2 + 2 * graph.node_count
    assert DependencyGraph.from_dict(json.loads(first["output"][0])).to_dict() == json.loads(
        first["output"][0]
    )


@pytest.mark.integration
def test_impact_results_are_byte_identical_across_hash_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real change through git, analysis, graph and impact: identical under PYTHONHASHSEED 0
    and 4242, and for a copy of the repository at a different absolute path."""
    hermetic_git_env(monkeypatch, tmp_path)
    repo = GitRepo.init(tmp_path / "repo")
    sources = {k: v for k, v in ANALYSIS_PROJECT.items() if k not in {"big.py", "broken.py"}}
    write_project(repo.root, sources)
    repo.write(
        "src/app/extra.py", "from app.models import Model\ndef make():\n    return Model()\n"
    )
    base = repo.commit_all("base")
    models = repo.root / "src" / "app" / "models.py"
    repo.write("src/app/models.py", models.read_text(encoding="utf-8") + "\n# edit\n")
    core = (repo.root / "src" / "app" / "core.py").read_text(encoding="utf-8")
    repo.write("src/app/core.py", core.replace("return f1()", "return f2()", 1))
    (repo.root / "src" / "app" / "extra.py").unlink()
    copy = tmp_path / "elsewhere" / "copy"
    shutil.copytree(repo.root, copy)
    first = _run([str(IMPACT_PROBE), str(repo.root), base], SEEDS[0])
    second = _run([str(IMPACT_PROBE), str(copy), base], SEEDS[1])
    assert first["set_order"] != second["set_order"]  # positive control
    assert first["output"] == second["output"]
    impact = json.loads(first["output"][1])
    # Non-trivial: changed and affected nodes, explanations and completeness reasons.
    assert len(impact["changed_nodes"]) > 2
    assert len(impact["affected_nodes"]) > 2
    assert impact["limitations"]
