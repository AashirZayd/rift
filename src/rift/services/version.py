"""Version use case: report the identity and version of this RIFT installation."""

from dataclasses import dataclass

from rift import __version__


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionInfo:
    """Identity of the running RIFT installation."""

    name: str
    version: str


def version_info() -> VersionInfo:
    """Return the name and version of this RIFT installation."""
    return VersionInfo(name="rift", version=__version__)
