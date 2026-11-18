"""SPEC §9.1 input handling: every unparseable file yields a structured diagnostic (or, for an
oversized file, a limitation) and every other file is still analyzed."""

import hashlib
import sys
from pathlib import Path

import pytest

from rift.analysis.python.analyzer import analyze_files
from rift.analysis.python.reading import FailureCode
from rift.core.models.analysis import AnalysisResult
from rift.core.models.diagnostic import DiagnosticSeverity
from rift.core.models.graph import UnresolvedReason
from tests.fixtures.python_project import analyze, edge_targets

GOOD = {
    "pkg/__init__.py": "",
    "pkg/good.py": """
        from pkg import bad
        def ok():
            return 1
    """,
}


def _assert_failed(result: AnalysisResult, code: FailureCode) -> None:
    assert [d.code for d in result.diagnostics] == [code.value]
    diagnostic = result.diagnostics[0]
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.subject == result.file_path
    assert diagnostic.location is not None
    assert [(u.reason, u.raw) for u in result.unresolved] == [
        (UnresolvedReason.SYNTAX_ERROR, code.value)
    ]
    # The module still exists as a node (impact on it is still visible); nothing else is guessed.
    assert [s.kind.value for s in result.symbols] == ["module"]
    assert result.edges == ()


def _assert_siblings_analyzed(results: dict[str, AnalysisResult]) -> None:
    good = results["pkg/good.py"]
    assert good.diagnostics == ()
    assert "py:pkg.good:ok" in {s.id for s in good.symbols}
    assert ("py:pkg.good", "py:pkg.bad", "certain") in edge_targets(good, "imports")


def test_oversized_file_is_skipped_with_a_limitation(tmp_path: Path) -> None:
    big = b"x = 1\n" * (5 * 1024 * 1024 // 6 + 1)  # > 5 MiB, SPEC §21.2
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": big})
    bad = results["pkg/bad.py"]
    assert [(lim.kind, lim.subject) for lim in bad.limitations] == [
        ("oversized_file", "pkg/bad.py")
    ]
    assert bad.diagnostics == ()
    assert bad.content_sha256 == hashlib.sha256(big).hexdigest()  # hashed in full, streaming
    assert [s.kind.value for s in bad.symbols] == ["module"]
    _assert_siblings_analyzed(results)


def test_max_file_bytes_is_configurable_and_inclusive(tmp_path: Path) -> None:
    source = b"x = 1\n"
    results = analyze(tmp_path, {"a.py": source}, max_file_bytes=len(source))
    assert results["a.py"].limitations == ()
    results = analyze(tmp_path, {"a.py": source}, max_file_bytes=len(source) - 1)
    assert [lim.kind for lim in results["a.py"].limitations] == ["oversized_file"]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(b"# -*- coding: bogus-codec -*-\nx = 1\n", id="unknown-codec"),
        pytest.param(b"x = '\xff\xfe'\n", id="invalid-utf8"),
        pytest.param(b"\xef\xbb\xbf# coding: latin-1\nx = 1\n", id="bom-vs-cookie"),
    ],
)
def test_bad_encoding_is_a_diagnostic(tmp_path: Path, source: bytes) -> None:
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": source})
    _assert_failed(results["pkg/bad.py"], FailureCode.ENCODING_ERROR)
    _assert_siblings_analyzed(results)


def test_declared_encodings_are_honoured(tmp_path: Path) -> None:
    latin = "# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n".encode("latin-1")
    bom = b"\xef\xbb\xbfVALUE = 1\n"
    results = analyze(tmp_path, {"latin.py": latin, "bom.py": bom})
    assert results["latin.py"].diagnostics == ()
    assert "py:latin:NAME" in {s.id for s in results["latin.py"].symbols}
    assert "py:bom:VALUE" in {s.id for s in results["bom.py"].symbols}


def test_null_bytes_are_a_diagnostic(tmp_path: Path) -> None:
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": b"x = 1\ny = '\x00'\n"})
    bad = results["pkg/bad.py"]
    _assert_failed(bad, FailureCode.NULL_BYTES)
    assert bad.diagnostics[0].location is not None
    assert bad.diagnostics[0].location.start_line == 2
    _assert_siblings_analyzed(results)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("x = " + "+".join(["1"] * 100_000) + "\n", id="recursion-error"),
        pytest.param("x = " + "-" * 100_000 + "1\n", id="parser-stack-overflow"),
    ],
)
def test_deeply_nested_code_is_a_diagnostic(tmp_path: Path, source: str) -> None:
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": source})
    _assert_failed(results["pkg/bad.py"], FailureCode.SOURCE_TOO_COMPLEX)
    _assert_siblings_analyzed(results)


def test_deep_but_parseable_expressions_are_analyzed_without_recursion(tmp_path: Path) -> None:
    """Expressions are walked with an explicit stack: a parseable deep tree is analyzed fully."""
    depth = 150  # below the parser's limits, above what naive recursion would comfortably take
    source = "def f():\n    return " + "[" * depth + "g()" + "]" * depth + "\ndef g():\n    pass\n"
    results = analyze(tmp_path, {"m.py": source})
    assert results["m.py"].diagnostics == ()
    assert ("py:m:f", "py:m:g", "certain") in edge_targets(results["m.py"], "calls")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def f(:\n    pass\n", id="invalid"),
        pytest.param("def f():\nreturn 1\n", id="indentation"),
        pytest.param("x = (\n", id="eof"),
    ],
)
def test_syntax_errors_are_diagnostics_with_a_position(tmp_path: Path, source: str) -> None:
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": source})
    bad = results["pkg/bad.py"]
    _assert_failed(bad, FailureCode.SYNTAX_ERROR)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert f"Python {version}" in bad.diagnostics[0].message
    assert bad.python_version == version
    _assert_siblings_analyzed(results)


# A construct one grammar step newer than each interpreter in the CI matrix.
_NEWER = {
    (3, 11): "type Alias = int\n",  # PEP 695 (3.12)
    (3, 12): "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n",  # PEP 758 (3.14)
    (3, 13): "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n",
}


def test_syntax_newer_than_the_interpreter_is_reported_as_such(tmp_path: Path) -> None:
    source = _NEWER.get(sys.version_info[:2])
    if source is None:
        pytest.skip("no grammar feature newer than this interpreter is known to the table")
    results = analyze(tmp_path, {**GOOD, "pkg/bad.py": source})
    bad = results["pkg/bad.py"]
    _assert_failed(bad, FailureCode.SYNTAX_NEWER_THAN_INTERPRETER)
    assert "run RIFT under Python" in bad.diagnostics[0].message
    _assert_siblings_analyzed(results)


def test_syntax_warnings_in_analyzed_code_do_not_escape(tmp_path: Path) -> None:
    # Invalid escapes warn at parse time; pytest runs with filterwarnings=error, so a leaked
    # warning would fail this test.
    results = analyze(tmp_path, {"m.py": "PATTERN = '\\d+'\n"})
    assert results["m.py"].diagnostics == ()


def test_unreadable_non_regular_entries_are_diagnostics(tmp_path: Path) -> None:
    (tmp_path / "dir.py").mkdir()
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    project = analyze_files(tmp_path, ["dir.py", "missing.py", "ok.py"])
    by_path = {r.file_path: r for r in project.results}
    assert [d.code for d in by_path["dir.py"].diagnostics] == ["not_a_regular_file"]
    assert [d.code for d in by_path["missing.py"].diagnostics] == ["unreadable_file"]
    assert "ENOENT" in by_path["missing.py"].diagnostics[0].message
    assert str(tmp_path) not in by_path["missing.py"].diagnostics[0].message
    assert by_path["ok.py"].diagnostics == ()
