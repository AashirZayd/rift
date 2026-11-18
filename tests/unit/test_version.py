"""Version smoke tests."""

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import rift
from rift.services.version import VersionInfo, version_info

REPO_ROOT = Path(__file__).resolve().parents[2]

# PEP 440 public version (release segment with optional pre/post/dev parts).
_PEP440 = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")


def test_version_is_pep440() -> None:
    assert _PEP440.fullmatch(rift.__version__)


def test_version_is_unreleased_until_m9() -> None:
    assert rift.__version__ == "0.0.0"


def test_version_is_single_sourced() -> None:
    # The only copy of the version is __version__ in src/rift/__init__.py: pyproject must
    # declare it dynamic and point hatchling there, never carry a static version of its own.
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in pyproject["project"]
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "src/rift/__init__.py"


def test_installed_metadata_matches_source_version() -> None:
    # Secondary check; a stale editable install (no `uv sync` after a bump) also fails here.
    assert version("rift-cli") == rift.__version__


def test_version_info_service() -> None:
    assert version_info() == VersionInfo(name="rift", version=rift.__version__)
