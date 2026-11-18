"""Schema mirror contract (ADR-0003): ``specs/`` byte-identically mirrors ``rift.core.schemas``.

The canonical schemas ship inside the package (``src/rift/core/schemas/``); ``specs/`` exists for
readers and external tooling. These tests make drift between the two copies impossible.
"""

from pathlib import Path

import pytest

import rift.core

REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = REPO_ROOT / "specs"
PACKAGE_DIR = Path(rift.core.__path__[0]) / "schemas"

# SPEC §4: the schemas RIFT v0.1 must ship.
EXPECTED_SCHEMAS = (
    "change-proof.schema.json",
    "config.schema.json",
    "invariants.schema.json",
)


def _schema_files(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.schema.json"))}


def test_mirror_has_no_drift() -> None:
    # Passes vacuously until schemas exist, but fails as soon as a schema is added to, changed
    # in, or removed from only one of the two locations.
    specs = _schema_files(SPECS_DIR)
    package = _schema_files(PACKAGE_DIR)
    assert sorted(specs) == sorted(package)
    for name in sorted(specs):
        assert specs[name] == package[name], f"{name} differs between specs/ and rift.core.schemas"


@pytest.mark.xfail(
    strict=True,
    reason="ADR-0003: schemas land in M6; remove this marker when they do (XPASS fails the suite)",
)
def test_expected_schemas_exist_in_both_locations_byte_identical() -> None:
    specs = _schema_files(SPECS_DIR)
    package = _schema_files(PACKAGE_DIR)
    for name in EXPECTED_SCHEMAS:
        assert name in package, f"missing canonical schema src/rift/core/schemas/{name}"
        assert name in specs, f"missing mirror specs/{name}"
        assert specs[name] == package[name], f"{name} differs between specs/ and rift.core.schemas"
