"""ADR-0007 item 9b: ``cls()`` in a classmethod and ``type(self)()`` in an instance method call
the defining class at ``likely`` (a subclass may be the receiver). Everything else that looks
similar stays ``unknown`` (item 9a)."""

from pathlib import Path

import pytest

from tests.fixtures.python_project import analyze, edge_targets, unresolved

BASE = {"pkg/__init__.py": ""}


def _analyze(tmp_path: Path, source: str) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    result = analyze(tmp_path, {**BASE, "pkg/m.py": source})["pkg/m.py"]
    return edge_targets(result, "calls"), unresolved(result)


@pytest.mark.parametrize(
    ("source", "caller", "target"),
    [
        pytest.param(
            "class A:\n    @classmethod\n    def make(cls):\n        return cls()\n",
            "py:pkg.m:A.make",
            "py:pkg.m:A",
            id="cls-in-classmethod",
        ),
        pytest.param(
            "import builtins\nclass A:\n    @builtins.classmethod\n    def make(cls):\n"
            "        return cls()\n",
            "py:pkg.m:A.make",
            "py:pkg.m:A",
            id="builtins-classmethod",
        ),
        pytest.param(
            "class A:\n    def __init_subclass__(cls):\n        cls()\n",
            "py:pkg.m:A.__init_subclass__",
            "py:pkg.m:A",
            id="implicit-classmethod",
        ),
        pytest.param(
            "class A:\n    def copy(self):\n        return type(self)()\n",
            "py:pkg.m:A.copy",
            "py:pkg.m:A",
            id="type-of-self",
        ),
        pytest.param(
            "class Outer:\n    class Inner:\n        @classmethod\n        def make(klass):\n"
            "            return klass()\n",
            "py:pkg.m:Outer.Inner.make",
            "py:pkg.m:Outer.Inner",
            id="nested-class-any-receiver-name",
        ),
        pytest.param(
            "class A:\n    @classmethod\n    def make(cls):\n        def build():\n"
            "            return cls()\n        return build()\n",
            "py:pkg.m:A.make.<locals>.build",
            "py:pkg.m:A",
            id="closure-inside-classmethod",
        ),
    ],
)
def test_receiver_instantiation_calls_the_defining_class_likely(
    tmp_path: Path, source: str, caller: str, target: str
) -> None:
    calls, refs = _analyze(tmp_path, source)
    assert (caller, target, "likely") in calls
    assert not {raw for reason, raw in refs if reason == "unknown"} & {"cls", "klass", "type(self)"}


@pytest.mark.parametrize(
    ("source", "raw"),
    [
        pytest.param(
            "class A:\n    @staticmethod\n    def make(cls):\n        return cls()\n",
            "cls",
            id="staticmethod-parameter",
        ),
        pytest.param(
            "class A:\n    def run(self):\n        return self()\n",
            "self",
            id="self-call-is-dunder-call",
        ),
        pytest.param(
            "class A:\n    @classmethod\n    def make(cls):\n        return type(cls)()\n",
            "type(cls)",
            id="type-of-cls-is-the-metaclass",
        ),
        pytest.param(
            "class A:\n    @classmethod\n    def make(cls, other):\n        cls = other\n"
            "        return cls()\n",
            "cls",
            id="rebound-receiver",
        ),
        pytest.param(
            "class A:\n    def copy(self, type):\n        return type(self)()\n",
            "type(self)",
            id="shadowed-type",
        ),
        pytest.param("def make(cls):\n    return cls()\n", "cls", id="plain-function"),
    ],
)
def test_lookalikes_stay_unknown(tmp_path: Path, source: str, raw: str) -> None:
    calls, refs = _analyze(tmp_path, source)
    assert ("unknown", raw) in refs
    assert not {c for c in calls if c[1] == "py:pkg.m:A"}
