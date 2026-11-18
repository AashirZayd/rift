"""Read, decode and parse one source file without ever executing it (SPEC §9.1).

``ast.parse`` only builds a syntax tree (it compiles with ``PyCF_ONLY_AST``): no bytecode is
produced, nothing is imported, no ``__pycache__`` is written. Every way a file can fail to become a
tree is turned into a :class:`ReadFailure`; nothing raises past this module.
"""

import ast
import errno
import hashlib
import io
import os
import stat
import sys
import tokenize
import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from rift.analysis.python.syntax import newer_syntax

_CHUNK = 1 << 16


class FailureCode(StrEnum):
    """Diagnostic codes for files that could not be parsed (ADR-0007)."""

    NOT_A_REGULAR_FILE = "not_a_regular_file"
    UNREADABLE_FILE = "unreadable_file"
    ENCODING_ERROR = "encoding_error"
    NULL_BYTES = "null_bytes"
    SYNTAX_ERROR = "syntax_error"
    SYNTAX_NEWER_THAN_INTERPRETER = "syntax_newer_than_interpreter"
    SOURCE_TOO_COMPLEX = "source_too_complex"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadFailure:
    code: FailureCode
    message: str
    line: int  # 1-based; 1 when the problem has no position
    col: int  # 0-based


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFile:
    """Exactly one of: ``tree`` (parsed), ``failure``, or ``oversized``. ``sha256`` is always the
    hash of the file's full bytes (empty-string hash if it could not be read at all)."""

    path: str
    sha256: str
    size: int
    oversized: bool
    text: str | None
    tree: ast.Module | None
    failure: ReadFailure | None


def _contained(root: Path, path: str) -> bool:
    """No path component is a link or junction and the file resolves inside ``root``: the
    resolved path must equal the lexical one (SPEC §21.2, ADR-0008)."""
    real_root = root.resolve(strict=True)
    real = (root / path).resolve(strict=True)
    return real == real_root.joinpath(*path.split("/")) and real.is_relative_to(real_root)


class _SwappedError(OSError):
    """The opened file is not the one that was checked (a component was swapped)."""


def _hash_and_read(
    full: Path, keep: int, *, expected: os.stat_result
) -> tuple[str, int, bytes | None]:
    """Stream the file: hash everything, keep the bytes only if there are at most ``keep``.
    The opened descriptor must be the file that was checked (narrows the check/open race)."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    kept = bytearray()
    size = 0
    fd = os.open(full, flags)
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
        os.close(fd)
        raise _SwappedError(errno.ELOOP, "file changed between check and open")
    with os.fdopen(fd, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            if size <= keep:
                kept += chunk
    return digest.hexdigest(), size, bytes(kept) if size <= keep else None


def _failed(path: str, sha: str, size: int, failure: ReadFailure) -> SourceFile:
    return SourceFile(
        path=path, sha256=sha, size=size, oversized=False, text=None, tree=None, failure=failure
    )


def read_source(root: Path, path: str, *, max_bytes: int) -> SourceFile:  # noqa: PLR0911  # one exit per failure mode
    """Read ``root / path`` (``path`` is repo-relative POSIX) and parse it."""
    full = root / path
    empty_sha = hashlib.sha256(b"").hexdigest()
    try:
        info = full.lstat()
        contained = _contained(root, path)
    except OSError as exc:
        return _failed(path, empty_sha, 0, _os_failure(exc))
    if not stat.S_ISREG(info.st_mode) or not contained:  # links are never followed (ADR-0008)
        return _failed(
            path,
            empty_sha,
            0,
            ReadFailure(
                code=FailureCode.NOT_A_REGULAR_FILE,
                message="not a regular file (links are never followed)",
                line=1,
                col=0,
            ),
        )
    try:
        sha, size, data = _hash_and_read(full, max_bytes, expected=info)
    except OSError as exc:  # permission denied, or swapped for a link (O_NOFOLLOW → ELOOP)
        return _failed(path, empty_sha, 0, _os_failure(exc))
    if data is None:
        return SourceFile(
            path=path, sha256=sha, size=size, oversized=True, text=None, tree=None, failure=None
        )
    text_or_failure = _decode(data)
    if isinstance(text_or_failure, ReadFailure):
        return _failed(path, sha, size, text_or_failure)
    text = text_or_failure
    parsed = _parse(text, path)
    if isinstance(parsed, ReadFailure):
        return _failed(path, sha, size, parsed)
    return SourceFile(
        path=path, sha256=sha, size=size, oversized=False, text=text, tree=parsed, failure=None
    )


def _os_failure(exc: OSError) -> ReadFailure:
    name = errno.errorcode.get(exc.errno or 0, "os error")  # never strerror: may embed paths
    return ReadFailure(
        code=FailureCode.UNREADABLE_FILE, message=f"cannot read file ({name})", line=1, col=0
    )


def _decode(data: bytes) -> str | ReadFailure:
    """PEP 263 decoding exactly as the interpreter does it (BOM, coding cookie, UTF-8)."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        text = data.decode(encoding)
    except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
        detail = exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__
        return ReadFailure(
            code=FailureCode.ENCODING_ERROR,
            message=f"cannot decode source: {detail}",
            line=1,
            col=0,
        )
    if text.startswith("\ufeff"):  # "utf-8-sig" strips it; an explicit cookie may not
        text = text[1:]
    if "\x00" in text:
        line = text.count("\n", 0, text.index("\x00")) + 1
        return ReadFailure(
            code=FailureCode.NULL_BYTES, message="source contains null bytes", line=line, col=0
        )
    return text


def _parse(text: str, path: str) -> ast.Module | ReadFailure:
    version = sys.version_info[:2]
    label = f"Python {version[0]}.{version[1]}"
    try:
        with warnings.catch_warnings():
            # Invalid escapes etc. warn at parse time; analyzed code must not spam our stderr
            # (or fail a run that treats warnings as errors).
            warnings.simplefilter("ignore")
            return ast.parse(text, filename=path, type_comments=False)
    except SyntaxError as exc:  # includes IndentationError / TabError
        line = max(exc.lineno or 1, 1)
        col = max((exc.offset or 1) - 1, 0)
        lines = text.splitlines()
        source_line = lines[line - 1] if line <= len(lines) else ""
        future = newer_syntax(source_line, (version[0], version[1]))
        if future is not None:
            since = f"{future.since[0]}.{future.since[1]}"
            return ReadFailure(
                code=FailureCode.SYNTAX_NEWER_THAN_INTERPRETER,
                message=(
                    f"{label} cannot parse this file; line {line} looks like {future.feature} "
                    f"(Python {since}+): run RIFT under Python {since} or newer"
                ),
                line=line,
                col=col,
            )
        return ReadFailure(
            code=FailureCode.SYNTAX_ERROR,
            message=f"{label} cannot parse this file: {exc.msg}",
            line=line,
            col=col,
        )
    except ValueError as exc:  # e.g. null bytes on some versions; never leaks a raw error
        return ReadFailure(
            code=FailureCode.SYNTAX_ERROR,
            message=f"{label} cannot parse this file: {type(exc).__name__}",
            line=1,
            col=0,
        )
    except (RecursionError, MemoryError):
        return ReadFailure(
            code=FailureCode.SOURCE_TOO_COMPLEX,
            message=f"{label} cannot parse this file: nesting is too deep for the parser",
            line=1,
            col=0,
        )
