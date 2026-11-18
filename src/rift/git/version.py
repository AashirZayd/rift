"""git version detection and the minimum supported version (ADR-0006).

Minimum: **2.30**. The binding requirement is ``--end-of-options`` (git 2.24), which SPEC §8
mandates for revision validation; other features used are older (``rev-parse
--is-shallow-repository`` 2.15, ``config --show-scope`` 2.26). 2.30 is the version CI proves in a
Debian bullseye container (git 2.30.2), with headroom over the hard floor.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rift.core.errors import ErrorCode
from rift.git.errors import GitUnavailableError
from rift.git.runner import DEFAULT_TIMEOUT, run_git

MIN_GIT_VERSION: tuple[int, int, int] = (2, 30, 0)
_VERSION = re.compile(r"\bgit version (\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True, slots=True, kw_only=True, order=True)
class GitVersion:
    major: int
    minor: int
    patch: int
    raw: str = ""

    @property
    def triple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_git_version(output: str) -> GitVersion:
    """Parse ``git --version`` output, e.g. ``git version 2.39.3 (Apple Git-146)``."""
    match = _VERSION.search(output)
    if match is None:
        raise GitUnavailableError(f"unrecognized `git --version` output: {output.strip()!r}")
    major, minor, patch = match.group(1), match.group(2), match.group(3) or "0"
    return GitVersion(major=int(major), minor=int(minor), patch=int(patch), raw=output.strip())


def is_supported(version: GitVersion) -> bool:
    return version.triple >= MIN_GIT_VERSION


def git_version(*, timeout: float = DEFAULT_TIMEOUT) -> GitVersion:
    """Return the installed git version (run outside any repository)."""
    result = run_git(["--version"], cwd=Path(tempfile.gettempdir()), timeout=timeout)
    return parse_git_version(result.stdout.decode("utf-8", errors="replace"))


def require_supported_git(*, timeout: float = DEFAULT_TIMEOUT) -> GitVersion:
    """Return the git version, or raise :class:`GitUnavailableError` if it is too old."""
    version = git_version(timeout=timeout)
    if not is_supported(version):
        minimum = ".".join(str(part) for part in MIN_GIT_VERSION)
        raise GitUnavailableError(
            f"git {version} is too old; RIFT requires git >= {minimum}",
            code=ErrorCode.GIT_TOO_OLD,
            hint=f"upgrade git to {minimum} or newer",
        )
    return version
