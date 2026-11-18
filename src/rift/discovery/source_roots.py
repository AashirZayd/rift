"""Source-root autodetection (SPEC §9.3, ADR-0008).

A source root is a repo-relative directory that module names are derived from; ``"."`` is the
repository root. Default: ``src`` if it contains importable Python, else the repository root.
"""

from collections.abc import Sequence

from rift.core.ids import is_dotted_module
from rift.core.paths import is_repo_path
from rift.discovery.files import DiscoveryError

REPO_ROOT = "."
SRC = "src"


def importable_name(rel_path: str) -> str | None:
    """Dotted module name of a ``.py`` path relative to its source root, or ``None`` when some
    segment is not an identifier (such a file cannot be imported by name)."""
    if not rel_path.endswith(".py"):
        return None
    parts = rel_path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    name = ".".join(parts)
    return name if name and is_dotted_module(name) else None


def detect_source_roots(files: Sequence[str]) -> tuple[str, ...]:
    """``("src",)`` if any discovered file under ``src/`` has an importable module name
    (regular or namespace package, or a top-level module), else ``(".",)``. A ``src/`` that is
    itself a package (``src/__init__.py``) is imported as ``src.x``, so it is not a root."""
    prefix = SRC + "/"
    if f"{prefix}__init__.py" in files:
        return (REPO_ROOT,)
    for path in files:
        if path.startswith(prefix) and importable_name(path[len(prefix) :]) is not None:
            return (SRC,)
    return (REPO_ROOT,)


def validate_source_root(root: str) -> str:
    """``"."`` or a normalized repo-relative directory path; raises :class:`DiscoveryError`."""
    if root == REPO_ROOT or is_repo_path(root):
        return root
    raise DiscoveryError(f"source root must be '.' or a repo-relative path, got {root!r}")
