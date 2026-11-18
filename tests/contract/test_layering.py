"""Layering contract (SPEC §4.1): import-linter passes on the real tree and rejects violations.

The negative cases copy ``src/rift`` and ``pyproject.toml`` into ``tmp_path``, plant one forbidden
import, and run the real ``lint-imports`` against the copy. This proves the configured contracts
bite, not merely that the current tree happens to satisfy them.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_LINT_IMPORTS = "from importlinter.cli import lint_imports_command; lint_imports_command()"


def _lint_imports(project: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(project / "src"),
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": "250",  # keep each contract's name and KEPT/BROKEN status on one line
    }
    return subprocess.run(
        [sys.executable, "-c", _LINT_IMPORTS, "--config", str(project / "pyproject.toml")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    shutil.copytree(
        REPO_ROOT / "src" / "rift",
        tmp_path / "src" / "rift",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


def _plant(project: Path, module_path: str, line: str) -> None:
    target = project / "src" / "rift" / module_path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(existing + line + "\n", encoding="utf-8")


def test_real_tree_keeps_every_contract() -> None:
    proc = _lint_imports(REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 broken" in proc.stdout


def test_unmodified_copy_keeps_every_contract(project_copy: Path) -> None:
    # Guards the negative tests below: a failure there must come from the planted import.
    proc = _lint_imports(project_copy)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("plants", "broken_contract"),
    [
        pytest.param(
            [("core/__init__.py", "import rift.cli")],
            "core imports nothing from rift",
            id="core-imports-cli",
        ),
        pytest.param(
            [("graph/__init__.py", "import rift.analysis")],
            "SPEC 4.1 layers",
            id="lower-imports-higher",
        ),
        pytest.param(
            [("graph/__init__.py", "import rift.git")],
            "SPEC 4.1 layers",
            id="l2-siblings-independent",
        ),
        pytest.param(
            [("impact/__init__.py", "import rift.invariants")],
            "SPEC 4.1 layers",
            id="l4-siblings-independent",
        ),
        pytest.param(
            [("cli/app.py", "import rift.graph")],
            "cli imports only services and core",
            id="cli-imports-graph",
        ),
        pytest.param(
            [("proof/__init__.py", "import rift.analysis")],
            "proof takes finished values",
            id="proof-imports-analysis",
        ),
        pytest.param(
            [
                ("proof/__init__.py", "import rift.policy"),
                ("policy/__init__.py", "import rift.verification"),
            ],
            "proof takes finished values",
            id="proof-reaches-verification-indirectly",
        ),
        pytest.param(
            [("plugins/api.py", "import rift.cache")],
            "plugins.api imports only rift.core",
            id="plugin-api-imports-cache",
        ),
        pytest.param(
            [("newlayer/__init__.py", '"""Unplaced subpackage."""')],
            "SPEC 4.1 layers",
            id="unlisted-subpackage",
        ),
    ],
)
def test_planted_violation_is_rejected(
    project_copy: Path, plants: list[tuple[str, str]], broken_contract: str
) -> None:
    for module_path, line in plants:
        _plant(project_copy, module_path, line)
    proc = _lint_imports(project_copy)
    assert proc.returncode != 0, proc.stdout
    broken_lines = [ln for ln in proc.stdout.splitlines() if ln.rstrip().endswith("BROKEN")]
    assert any(broken_contract in ln for ln in broken_lines), proc.stdout
