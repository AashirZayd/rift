"""The single entry point for running git (SPEC §8). Every git call in RIFT goes through here.

Hardening, in addition to the SPEC §8 ``-c`` overrides and environment (ADR-0006):

- git is resolved to an absolute path from *absolute* ``PATH`` entries only, so a planted
  ``git``/``git.exe`` in the analyzed repository or the current directory is never executed (on
  Windows, process creation would otherwise search the current directory first).
- ``--no-pager`` and a closed stdin: git can never page or prompt.
- No network, ever (SPEC §2.4): ``-c protocol.allow=never`` blocks every transport, which also stops
  a partial clone from lazily fetching missing objects through repository-configured transport
  commands (``remote.*.uploadpack``, ``core.sshCommand``); ``GIT_NO_LAZY_FETCH=1`` (honoured by
  git >= 2.44) disables lazy fetching outright.
- Callers may add per-repository ``-c`` overrides (e.g. neutralized filter drivers).
"""

import os
import subprocess
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from rift.core.errors import ErrorCode
from rift.git.errors import (
    GitError,
    GitUnavailableError,
    NotARepositoryError,
    UnsafeRepositoryError,
)

HARDENING_ARGS: tuple[str, ...] = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "diff.external=",
    "-c",
    "core.pager=cat",
    "-c",
    "color.ui=false",
)
# Beyond SPEC §8 (ADR-0006): RIFT never touches the network.
NO_NETWORK_ARGS: tuple[str, ...] = ("-c", "protocol.allow=never")
DIFF_ARGS: tuple[str, ...] = ("--no-ext-diff", "--no-textconv", "--no-color")
GIT_ENV: Mapping[str, str] = MappingProxyType(
    {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
        "GIT_PAGER": "cat",
        "GIT_NO_LAZY_FETCH": "1",  # beyond SPEC §8 (ADR-0006): never fetch missing objects
    }
)
DEFAULT_TIMEOUT = 60.0
_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 2000


@dataclass(frozen=True, slots=True, kw_only=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


def locate_git(path_env: str | None = None) -> Path:
    """Find the git executable on ``PATH``, considering only absolute directories."""
    search = os.environ.get("PATH", "") if path_env is None else path_env
    name = "git.exe" if os.name == "nt" else "git"
    for entry in search.split(os.pathsep):
        directory = Path(entry) if entry else None
        if directory is None or not directory.is_absolute():
            continue
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise GitUnavailableError(
        "git executable not found on PATH",
        hint="install git (>= 2.30) and make sure it is on PATH",
    )


def stderr_tail(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")
    tail = "\n".join(text.strip().splitlines()[-_STDERR_TAIL_LINES:])
    return tail[-_STDERR_TAIL_CHARS:]


def _classify_failure(result: GitResult, cwd: Path) -> GitError:
    tail = stderr_tail(result.stderr)
    lowered = tail.lower()
    command = result.args[0] if result.args else "git"
    if "dubious ownership" in lowered or "safe.directory" in lowered:
        return UnsafeRepositoryError(
            "git refused the repository because it is owned by another user",
            hint="if you trust it, run: git config --global --add safe.directory <path> "
            "(RIFT never changes git config)",
            stderr_tail=tail,
        )
    refused_transport = "transport '" in lowered and "not allowed" in lowered
    if refused_transport or "promisor" in lowered or "lazy fetch" in lowered:
        return GitError(
            "git needs objects that are missing locally (partial or incomplete clone)",
            code=ErrorCode.MISSING_OBJECTS,
            hint="RIFT never fetches; fetch the needed history first (e.g. a full clone, or "
            "`fetch-depth: 0` without `filter:` in actions/checkout)",
            stderr_tail=tail,
        )
    if "not a git repository" in lowered or "must be run in a work tree" in lowered:
        return NotARepositoryError(
            f"not inside a git working tree: {cwd}",
            hint="run RIFT inside a git working tree or pass --repo PATH",
            stderr_tail=tail,
        )
    return GitError(
        f"git {command} failed with exit code {result.returncode}",
        stderr_tail=tail,
        context={"exit_code": str(result.returncode)},
    )


def config_overrides(pairs: Sequence[str]) -> tuple[str, ...]:
    """Turn ``key=value`` strings into ``-c key=value`` argument pairs."""
    args: list[str] = []
    for pair in pairs:
        if "=" not in pair or pair.startswith("-"):
            raise GitError(f"invalid git config override: {pair!r}")
        args.extend(("-c", pair))
    return tuple(args)


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT,
    ok_returncodes: Collection[int] = (0,),
    extra_config: Sequence[str] = (),
) -> GitResult:
    """Run ``git <hardening> <args>`` in ``cwd`` with an argv list (never a shell).

    Exit codes outside ``ok_returncodes`` raise a :class:`GitError` subclass; so do timeouts and
    a missing executable. ``extra_config`` holds additional ``key=value`` overrides.
    """
    argv = [
        str(locate_git()),
        "--no-pager",
        *HARDENING_ARGS,
        *NO_NETWORK_ARGS,
        *config_overrides(extra_config),
        *args,
    ]
    env = {**os.environ, **GIT_ENV}
    try:
        completed = subprocess.run(  # noqa: S603  # argv list from fixed tokens + validated args; no shell
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git {args[0] if args else ''} timed out after {timeout:g}s",
            code=ErrorCode.GIT_TIMEOUT,
            stderr_tail=stderr_tail(exc.stderr or b""),
        ) from exc
    except FileNotFoundError as exc:
        if not cwd.is_dir():
            raise NotARepositoryError(f"directory does not exist: {cwd}") from exc
        raise GitUnavailableError("git executable could not be started") from exc
    except OSError as exc:
        raise GitUnavailableError(f"git executable could not be started: {exc}") from exc
    result = GitResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if completed.returncode not in ok_returncodes:
        raise _classify_failure(result, cwd)
    return result
