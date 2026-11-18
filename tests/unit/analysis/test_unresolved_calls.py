"""SPEC §2.3/§9.3: a call whose callee cannot be resolved is recorded, never silently dropped.

ADR-0007 item 9a: a bare callee bound to something the resolver does not understand (a local,
parameter or plain assignment), a subscript, or the result of a call is ``unknown`` with the
callee text in ``raw``. Builtins, external modules and directly called lambdas are not.
"""

from pathlib import Path

import pytest

from tests.fixtures.python_project import analyze, edge_targets, unresolved

BASE = {"pkg/__init__.py": "", "pkg/v.py": "handler = None\n"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "def f():\n    pass\ndef g():\n    cb = f\n    cb()\n",
            {("unknown", "cb")},
            id="local-alias-of-function",
        ),
        pytest.param(
            "def f():\n    pass\ncb = f\ncb()\n", {("unknown", "cb")}, id="module-alias-of-function"
        ),
        pytest.param("def g(cb):\n    cb()\n", {("unknown", "cb")}, id="parameter"),
        pytest.param("def g(hs):\n    hs[0]()\n", {("unknown", "hs[0]")}, id="subscript"),
        pytest.param(
            "def g(h):\n    h()()\n", {("unknown", "h"), ("unknown", "h()")}, id="call-result"
        ),
        pytest.param(
            "from pkg.v import handler\nhandler()\n",
            {("unknown", "handler")},
            id="imported-variable",
        ),
    ],
)
def test_unresolvable_call_is_recorded_as_unknown(
    tmp_path: Path, source: str, expected: set[tuple[str, str]]
) -> None:
    result = analyze(tmp_path, {**BASE, "pkg/m.py": source})["pkg/m.py"]
    assert unresolved(result) == expected
    assert edge_targets(result, "calls") == set()


def test_alias_keeps_its_reference_edge_alongside_the_unknown_call(tmp_path: Path) -> None:
    result = analyze(
        tmp_path, {**BASE, "pkg/m.py": "def f():\n    pass\ndef g():\n    cb = f\n    cb()\n"}
    )["pkg/m.py"]
    assert edge_targets(result, "references") == {("py:pkg.m:g", "py:pkg.m:f", "certain")}
    (ref,) = result.unresolved
    assert (ref.source, ref.location.start_line) == ("py:pkg.m:g", 5)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("print(1)\nlen([])\n", id="builtins"),
        pytest.param("(lambda: 1)()\n", id="lambda-literal"),
        pytest.param("class A:\n    pass\nA()\n", id="project-class"),
        pytest.param("def g():\n    def h():\n        pass\n    h()\n", id="nested-def"),
    ],
)
def test_resolved_or_settled_calls_add_no_unknown(tmp_path: Path, source: str) -> None:
    result = analyze(tmp_path, {**BASE, "pkg/m.py": source})["pkg/m.py"]
    assert unresolved(result) == set()


def test_external_call_is_covered_by_the_import_record_only(tmp_path: Path) -> None:
    source = "import os\nfrom os import getcwd\nos.getcwd()\ngetcwd()\n"
    result = analyze(tmp_path, {**BASE, "pkg/m.py": source})["pkg/m.py"]
    assert unresolved(result) == {("external_module", "os")}
