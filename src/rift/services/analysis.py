"""Analysis use case: which files RIFT analyzes, and analyzing them (ADR-0010 §1).

The analyzed file set is **git's view intersected with discovery**:
- git lists tracked files plus untracked files that are not ignored;
- discovery lists the ``.py`` files that are safe to read, without following links and after
  the configured exclusions.

So a ``.gitignore``d ``venv/`` or build output is never analyzed, a nested repository is not part
of this one, and a tracked file deleted from disk is not read.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rift.analysis.python.analyzer import ProjectAnalysis, analyze_files
from rift.analysis.python.options import AnalysisOptions
from rift.core.models.evidence import Limitation
from rift.core.models.repo import Repository
from rift.discovery.files import discover_python_files
from rift.git.files import list_worktree_files
from rift.git.runner import DEFAULT_TIMEOUT


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyzedFileSet:
    """Repo-relative ``.py`` paths to analyze (sorted), and the discovery limitations that concern
    them (a skipped link or directory that git lists, or that contains files git lists)."""

    files: tuple[str, ...]
    limitations: tuple[Limitation, ...]


def analyzed_file_set(
    repo: Repository, *, exclude: Sequence[str] = (), timeout: float = DEFAULT_TIMEOUT
) -> AnalyzedFileSet:
    """git (tracked + untracked-not-ignored) ∩ discovery."""
    listed = frozenset(list_worktree_files(repo, timeout=timeout))
    discovered = discover_python_files(repo.root, exclude=exclude)
    directories = {
        "/".join(parts[:i])
        for path in listed
        for parts in [path.split("/")]
        for i in range(1, len(parts))
    }
    relevant = listed | directories
    return AnalyzedFileSet(
        files=tuple(path for path in discovered.files if path in listed),
        limitations=tuple(
            lim for lim in discovered.limitations if lim.subject is None or lim.subject in relevant
        ),
    )


def analyze_repository(
    repo: Repository,
    *,
    options: AnalysisOptions | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProjectAnalysis:
    """Analyze the working tree's analyzed file set (never ignored or untracked-ignored files)."""
    opts = options or AnalysisOptions()
    file_set = analyzed_file_set(repo, exclude=opts.exclude, timeout=timeout)
    return analyze_files(repo.root, file_set.files, options=opts, limitations=file_set.limitations)
