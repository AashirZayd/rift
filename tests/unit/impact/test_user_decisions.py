"""The user's decisions on M4 (ADR-0010 §9 amendment).

1. ``external_module`` references stay in ``unresolved`` but do not affect completeness, with a
   guard: an import whose top-level name is a project package or module is never external, and
   a broken internal import is ``missing_module`` and still makes the result partial.
3. Non-literal dynamic imports anywhere in the project add one informational, sorted entry
   naming the files; completeness is unchanged.
"""

import dataclasses
from pathlib import Path

import pytest

from rift.core.ids import NodeId
from rift.core.models.graph import UnresolvedReason, UnresolvedReference
from rift.core.models.impact import Completeness
from rift.core.models.source import SourceLocation
from rift.graph.builder import build_graph
from rift.impact.assess import assess_impact
from rift.impact.index import ProjectIndex
from rift.impact.mapping import map_changes
from tests.unit.impact.helpers import analyze, impact_of_change, modified

CORE_BODY = "def _helper():\n    return 1\n"


@pytest.mark.parametrize(
    "imports",
    [
        pytest.param("import os\nimport json.decoder\n", id="stdlib"),
        pytest.param("import requests\nfrom numpy import array\n", id="third-party"),
    ],
)
def test_external_imports_are_listed_but_do_not_make_it_partial(
    tmp_path: Path, imports: str
) -> None:
    analysis = analyze(tmp_path, {"pkg/__init__.py": "", "pkg/core.py": imports + CORE_BODY})
    line = imports.count("\n") + 2  # inside _helper
    result = impact_of_change(analysis, [modified("pkg/core.py", line)])
    assert {u.reason for u in result.unresolved} == {UnresolvedReason.EXTERNAL_MODULE}
    assert (result.completeness, result.limitations) == (Completeness.COMPLETE, ())


def test_broken_internal_import_is_missing_module_and_partial(tmp_path: Path) -> None:
    source = "import pkg.missing\nimport os\n" + CORE_BODY
    analysis = analyze(tmp_path, {"pkg/__init__.py": "", "pkg/core.py": source})
    result = impact_of_change(analysis, [modified("pkg/core.py", 4)])
    assert {(u.reason, u.raw) for u in result.unresolved} == {
        (UnresolvedReason.MISSING_MODULE, "pkg.missing"),
        (UnresolvedReason.EXTERNAL_MODULE, "os"),
    }
    assert result.completeness is Completeness.PARTIAL
    assert [(lim.kind, lim.subject) for lim in result.limitations] == [
        ("unresolved_reference", "missing_module")
    ]


def test_guard_external_reference_naming_a_project_package_still_counts(tmp_path: Path) -> None:
    """Even if a reference were labelled external_module, a project top-level name makes it
    count: the project's own code is never assumed external."""
    analysis = analyze(tmp_path, {"pkg/__init__.py": "", "pkg/core.py": CORE_BODY})
    build = build_graph(analysis.results)
    location = SourceLocation(path="pkg/core.py", start_line=1, start_col=0, end_line=1, end_col=1)
    mislabelled = UnresolvedReference(
        source=NodeId("py:pkg.core:_helper"),
        raw="pkg.vendored",
        reason=UnresolvedReason.EXTERNAL_MODULE,
        location=location,
    )
    build = dataclasses.replace(build, unresolved=(*build.unresolved, mislabelled))
    index = ProjectIndex(analysis)
    mapping = map_changes([modified("pkg/core.py", 2)], index)
    result = assess_impact(analysis=analysis, build=build, mapping=mapping, index=index)
    assert result.completeness is Completeness.PARTIAL
    assert [(lim.kind, lim.subject) for lim in result.limitations] == [
        ("unresolved_reference", "external_module")
    ]


def test_project_wide_dynamic_imports_add_one_informational_entry(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "pkg/__init__.py": "",
        "pkg/core.py": CORE_BODY,
        "pkg/plugins.py": (
            "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"
        ),
        "pkg/boot.py": "import sys\nsys.path.append('x')\nexec('y = 1')\n",
        "pkg/literal.py": "import importlib\nm = importlib.import_module('pkg.core')\n",
    }
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    assert result.completeness is Completeness.COMPLETE  # informational only
    (note,) = result.limitations
    assert (note.kind, note.subject) == ("dynamic_code_in_project", None)
    assert note.message.endswith("pkg/boot.py, pkg/plugins.py")  # sorted; literal import excluded
    assert "2 file(s)" in note.message


def test_dynamic_code_note_names_the_first_50_files_then_a_count(tmp_path: Path) -> None:
    """Follow-up 2: the message is capped at 50 sorted paths plus "... and N more"."""
    dynamic = "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"
    files: dict[str, str | bytes] = {"pkg/__init__.py": "", "pkg/core.py": CORE_BODY}
    files.update({f"pkg/dyn_{n:02d}.py": dynamic for n in range(53)})
    analysis = analyze(tmp_path, files)
    result = impact_of_change(analysis, [modified("pkg/core.py", 2)])
    (note,) = result.limitations
    shown = ", ".join(f"pkg/dyn_{n:02d}.py" for n in range(50))
    assert note.message == (
        "non-literal dynamic imports or code loading in 53 file(s), any of which may load "
        f"changed code (does not affect completeness): {shown}, ... and 3 more"
    )
    again = impact_of_change(analyze(tmp_path / "again", files), [modified("pkg/core.py", 2)])
    assert again.limitations == result.limitations  # deterministic


def test_dynamic_code_note_at_exactly_50_files_has_no_suffix(tmp_path: Path) -> None:
    dynamic = "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"
    files: dict[str, str | bytes] = {"pkg/__init__.py": "", "pkg/core.py": CORE_BODY}
    files.update({f"pkg/dyn_{n:02d}.py": dynamic for n in range(50)})
    result = impact_of_change(analyze(tmp_path, files), [modified("pkg/core.py", 2)])
    (note,) = result.limitations
    assert note.message.endswith("pkg/dyn_48.py, pkg/dyn_49.py")
    assert "more" not in note.message
