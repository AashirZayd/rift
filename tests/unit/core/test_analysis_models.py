"""AnalysisResult / Diagnostic invariants and strict decoding (ADR-0007)."""

import dataclasses

import pytest

from rift.core.errors import ModelValidationError, SerializationError
from rift.core.ids import NodeId, SymbolId
from rift.core.models.analysis import AnalysisResult
from rift.core.models.diagnostic import Diagnostic, DiagnosticSeverity
from rift.core.models.evidence import Limitation
from rift.core.models.graph import (
    Confidence,
    Edge,
    EdgeKind,
    EdgeOrigin,
    UnresolvedReason,
    UnresolvedReference,
)
from rift.core.models.source import SourceLocation, Symbol, SymbolKind, Visibility
from rift.core.serialization import from_dict, to_dict

SHA = "a" * 64


def _loc(line: int = 1, path: str = "pkg/m.py") -> SourceLocation:
    return SourceLocation(path=path, start_line=line, start_col=0, end_line=line, end_col=1)


def _symbol(qualname: str) -> Symbol:
    return Symbol(
        id=SymbolId(f"py:pkg.m:{qualname}"),
        kind=SymbolKind.FUNCTION,
        name=qualname,
        qualname=qualname,
        module="pkg.m",
        location=_loc(),
        visibility=Visibility.PUBLIC,
        decorators=(),
    )


def _edge(source: str, target: str) -> Edge:
    return Edge(
        source=NodeId(source),
        target=NodeId(target),
        kind=EdgeKind.CALLS,
        origin=EdgeOrigin.NAME_RESOLUTION,
        confidence=Confidence.CERTAIN,
        evidence_ids=(),
        flags=frozenset(),
    )


def _result(**changes: object) -> AnalysisResult:
    base = AnalysisResult(
        file_path="pkg/m.py",
        module="pkg.m",
        content_sha256=SHA,
        analyzer_name="python",
        analyzer_version="0.1.0",
        python_version="3.11",
        symbols=(_symbol("a"), _symbol("b")),
        edges=(_edge("py:pkg.m:a", "py:pkg.m:b"),),
        unresolved=(
            UnresolvedReference(
                source=NodeId("py:pkg.m"),
                raw="os",
                reason=UnresolvedReason.EXTERNAL_MODULE,
                location=_loc(),
            ),
        ),
        diagnostics=(),
        limitations=(),
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def test_valid_result_round_trips() -> None:
    result = _result()
    assert from_dict(AnalysisResult, to_dict(result)) == result


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"symbols": (_symbol("b"), _symbol("a"))}, id="unsorted-symbols"),
        pytest.param({"symbols": (_symbol("a"), _symbol("a"))}, id="duplicate-symbols"),
        pytest.param(
            {"edges": (_edge("py:pkg.m:b", "py:x"), _edge("py:pkg.m:a", "py:x"))},
            id="unsorted-edges",
        ),
        pytest.param({"edges": (_edge("py:other:f", "py:x"),)}, id="foreign-edge-source"),
        pytest.param({"module": None}, id="unnamed-with-symbols"),
        pytest.param({"content_sha256": "A" * 64}, id="uppercase-sha"),
        pytest.param({"python_version": "3"}, id="bad-python-version"),
        pytest.param({"python_version": "3.١٢"}, id="non-ascii-digits"),
        pytest.param({"file_path": "/abs/m.py"}, id="absolute-path"),
        pytest.param(
            {
                "limitations": (
                    Limitation(kind="b", message="m", subject=None),
                    Limitation(kind="a", message="m", subject=None),
                )
            },
            id="unsorted-limitations",
        ),
        pytest.param(
            {
                "unresolved": (
                    UnresolvedReference(
                        source=NodeId("py:pkg.m"),
                        raw="x",
                        reason=UnresolvedReason.UNKNOWN,
                        location=_loc(path="other.py"),
                    ),
                )
            },
            id="unresolved-elsewhere",
        ),
    ],
)
def test_invalid_results_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ModelValidationError):
        _result(**changes)


def test_unnamed_file_uses_its_file_node() -> None:
    result = _result(
        module=None,
        symbols=(),
        edges=(_edge("file:pkg/m.py", "py:x"),),
        unresolved=(),
    )
    assert result.edges[0].source == "file:pkg/m.py"


@pytest.mark.parametrize("code", ["", "Syntax", "syntax-error", "1abc"])
def test_diagnostic_codes_are_snake_case(code: str) -> None:
    with pytest.raises(ModelValidationError):
        Diagnostic(
            severity=DiagnosticSeverity.ERROR, code=code, message="m", subject=None, location=None
        )


def test_diagnostic_decoding_is_strict() -> None:
    diag = Diagnostic(
        severity=DiagnosticSeverity.WARNING, code="x", message="m", subject="a.py", location=_loc()
    )
    data = to_dict(diag)
    assert from_dict(Diagnostic, data) == diag
    with pytest.raises(SerializationError):
        from_dict(Diagnostic, {**data, "extra": 1})
    with pytest.raises(SerializationError):
        from_dict(Diagnostic, {**data, "severity": "fatal"})
