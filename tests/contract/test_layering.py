"""Layering contract (SPEC §4.1): import-linter passes on the real tree and rejects violations.

The negative cases copy ``src/rift`` and ``pyproject.toml`` into ``tmp_path``, plant one forbidden
import, and run the real ``lint-imports`` against the copy. This proves the configured contracts
bite, not merely that the current tree happens to satisfy them.
"""

import os
import pkgutil
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

import rift

REPO_ROOT = Path(__file__).resolve().parents[2]
# The public console script installed next to the interpreter (Scripts\ on Windows, bin/ on POSIX).
LINT_IMPORTS = Path(sys.executable).with_name(
    "lint-imports.exe" if os.name == "nt" else "lint-imports"
)

TOP_LEVEL = frozenset(info.name for info in pkgutil.iter_modules(rift.__path__) if info.ispkg)


def _lint_imports(project: Path) -> subprocess.CompletedProcess[str]:
    assert LINT_IMPORTS.is_file(), f"lint-imports console script not found at {LINT_IMPORTS}"
    env = {
        **os.environ,
        "PYTHONPATH": str(project / "src"),
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": "250",  # keep each contract's name and KEPT/BROKEN status on one line
    }
    return subprocess.run(
        [str(LINT_IMPORTS), "--config", str(project / "pyproject.toml")],
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
        pytest.param(
            [("core/a.py", "import rift.core.b"), ("core/b.py", "import rift.core.a")],
            "no circular imports",
            id="cycle-inside-a-package",
        ),
        pytest.param(
            [
                ("plugins/registry.py", '"""Registry."""'),
                ("plugins/api.py", "import rift.plugins.registry"),
            ],
            "plugins internals",
            id="plugin-api-imports-registry",
        ),
        pytest.param(
            [("plugins/loader.py", '"""Unplaced plugins module."""')],
            "plugins internals",
            id="unplaced-plugins-module",
        ),
        pytest.param(
            [("policy/render.py", "import rich")],
            "only cli renders",
            id="policy-imports-rich",
        ),
        pytest.param(
            [("services/echo.py", "import typer")],
            "only cli renders",
            id="services-imports-typer",
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


def _contract(name_prefix: str) -> dict[str, Any]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    matches = [
        c for c in config["tool"]["importlinter"]["contracts"] if c["name"].startswith(name_prefix)
    ]
    assert len(matches) == 1, f"expected exactly one contract named {name_prefix!r}"
    contract: dict[str, Any] = matches[0]
    return contract


@pytest.mark.parametrize(
    ("name_prefix", "allowed", "extra"),
    [
        # Hand-maintained forbidden lists must track the real subpackage set, so a new
        # top-level package cannot silently become importable from these sources.
        ("cli imports only services and core", {"services", "core", "cli"}, set()),
        ("core imports nothing from rift", {"core"}, {"rift.__main__"}),
        # rift.plugins is the ancestor of the source; its internals are covered by the
        # "plugins internals" layers contract instead.
        ("plugins.api imports only rift.core", {"core", "plugins"}, {"rift.__main__"}),
    ],
)
def test_forbidden_lists_cover_every_other_subpackage(
    name_prefix: str, allowed: set[str], extra: set[str]
) -> None:
    contract = _contract(name_prefix)
    expected = {f"rift.{name}" for name in TOP_LEVEL - allowed} | extra
    assert set(contract["forbidden_modules"]) == expected


def test_renderer_contract_covers_every_non_cli_subpackage() -> None:
    contract = _contract("only cli renders")
    assert set(contract["source_modules"]) == {f"rift.{name}" for name in TOP_LEVEL - {"cli"}}
    assert set(contract["forbidden_modules"]) == {"rich", "typer"}


def test_layers_contract_places_every_subpackage() -> None:
    contract = _contract("SPEC 4.1 layers")
    placed = {part.strip() for layer in contract["layers"] for part in layer.split("|")}
    assert placed == TOP_LEVEL
    assert contract["exhaustive"] is True
