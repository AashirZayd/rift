"""ImpactResult invariants (SPEC §6.4, ADR-0010 §6): completeness reasons are listed, never just a
flag; every affected node and related test is explained; collections are sorted."""

from dataclasses import replace

import pytest

from rift.core.errors import ModelValidationError
from rift.core.ids import NodeId
from rift.core.models.evidence import Limitation
from rift.core.models.graph import UnresolvedReason, UnresolvedReference
from rift.core.models.impact import Completeness, ImpactClass, ImpactExplanation, ImpactResult
from rift.core.models.source import SourceLocation
from rift.core.serialization import from_dict, to_dict

A, B = NodeId("py:pkg.a:f"), NodeId("py:pkg.b:g")
T = NodeId("test:tests/test_a.py::test_f")
REASON = Limitation(kind="parse_failure", message="could not parse", subject="pkg/c.py")


def _ref(line: int, raw: str) -> UnresolvedReference:
    location = SourceLocation(
        path="pkg/b.py", start_line=line, start_col=0, end_line=line, end_col=1
    )
    return UnresolvedReference(
        source=B, raw=raw, reason=UnresolvedReason.UNKNOWN, location=location
    )


def _result(**overrides: object) -> ImpactResult:
    base: dict[str, object] = {
        "changed_nodes": (A,),
        "affected_nodes": (B, T),
        "public_interfaces": (A,),
        "related_tests": (T,),
        "unresolved": (),
        "classification": ImpactClass.MODULE,
        "completeness": Completeness.COMPLETE,
        "limitations": (),
        "explanations": (
            ImpactExplanation(node=B, path=(A, B)),
            ImpactExplanation(node=T, path=(A, T)),
        ),
    }
    return ImpactResult(**{**base, **overrides})  # type: ignore[arg-type]  # test builder


def test_complete_result_may_carry_informational_limitations() -> None:
    """ADR-0010 §9 (user decision): e.g. project-wide dynamic imports do not affect completeness."""
    note = Limitation(
        kind="dynamic_code_in_project", message="dynamic imports in x.py", subject=None
    )
    assert _result(limitations=(note,)).completeness is Completeness.COMPLETE


def test_valid_result_round_trips() -> None:
    result = _result(
        completeness=Completeness.PARTIAL,
        limitations=(REASON,),
        unresolved=(_ref(1, "x"), _ref(2, "y")),
    )
    assert from_dict(ImpactResult, to_dict(result)) == result


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"completeness": Completeness.PARTIAL}, id="partial-without-reasons"),
        pytest.param({"completeness": Completeness.UNKNOWN}, id="unknown-without-reasons"),
        pytest.param({"related_tests": (B,)}, id="non-test-related-test"),
        pytest.param(
            {"explanations": (ImpactExplanation(node=T, path=(A, T)),)},
            id="unexplained-affected-node",
        ),
        pytest.param({"unresolved": (_ref(2, "y"), _ref(1, "x"))}, id="unsorted-unresolved"),
        pytest.param({"limitations": (REASON,)}, id="complete-with-reasons"),
        pytest.param(
            {
                "completeness": Completeness.PARTIAL,
                "limitations": (
                    Limitation(kind="dynamic_code_in_project", message="x.py", subject=None),
                ),
            },
            id="partial-with-only-informational",
        ),
        pytest.param({"changed_nodes": (A, B)}, id="changed-and-affected"),
        pytest.param(
            {"public_interfaces": (NodeId("py:pkg.z:q"),)}, id="public-outside-impact-set"
        ),
        pytest.param(
            {
                "affected_nodes": (B, T, NodeId("test:tests/test_z.py::test_z")),
                "related_tests": (T, NodeId("test:tests/test_z.py::test_z")),
                "explanations": (
                    ImpactExplanation(node=B, path=(A, B)),
                    ImpactExplanation(node=T, path=(A, T)),
                    ImpactExplanation(
                        node=NodeId("test:tests/test_z.py::test_z"),
                        path=(B, NodeId("test:tests/test_z.py::test_z")),
                    ),
                ),
            },
            id="path-not-from-a-changed-node",
        ),
        pytest.param({"classification": ImpactClass.NONE}, id="none-with-changes"),
        pytest.param(
            {
                "completeness": Completeness.PARTIAL,
                "limitations": (REASON, replace(REASON, kind="analysis_skipped")),
            },
            id="unsorted-limitations",
        ),
    ],
)
def test_invalid_results_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ModelValidationError):
        _result(**overrides)
