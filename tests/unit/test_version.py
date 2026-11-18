"""Version smoke tests."""

import re
from importlib.metadata import version

import rift
from rift.services.version import VersionInfo, version_info

# PEP 440 public version (release segment with optional pre/post/dev parts).
_PEP440 = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")


def test_version_is_pep440() -> None:
    assert _PEP440.fullmatch(rift.__version__)


def test_version_is_unreleased_until_m9() -> None:
    assert rift.__version__ == "0.0.0"


def test_version_is_single_sourced() -> None:
    # The build backend reads __version__ from src/rift/__init__.py; installed
    # metadata must agree, proving there is no second copy of the version.
    assert version("rift-cli") == rift.__version__


def test_version_info_service() -> None:
    assert version_info() == VersionInfo(name="rift", version=rift.__version__)
