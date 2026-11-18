"""Canonical JSON rules (SPEC §7.1) and frozen values."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rift.core.canonical import (
    NonCanonicalValueError,
    canonical_bytes,
    canonical_json,
    sha256_hex,
)
from rift.core.errors import ErrorCode, ModelValidationError
from rift.core.frozen import FrozenMap, freeze, thaw

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=20,
)


def test_exact_canonical_form() -> None:
    value = {"b": [1, True, None], "a": "é ✓", "c": {"z": 1, "y": 2}}
    assert canonical_json(value) == '{"a":"é ✓","b":[1,true,null],"c":{"y":2,"z":1}}'
    assert canonical_bytes(value) == canonical_json(value).encode("utf-8")


def test_sha256_hex() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize(
    "bad",
    [1.5, float("nan"), float("inf"), {"a": 0.0}, [1, 2.0]],
    ids=["float", "nan", "inf", "nested-float", "float-in-list"],
)
def test_floats_are_rejected(bad: object) -> None:
    with pytest.raises(NonCanonicalValueError) as exc_info:
        canonical_json(bad)
    assert exc_info.value.code is ErrorCode.NON_CANONICAL_VALUE


@pytest.mark.parametrize(
    "bad",
    [{1: "a"}, {("a",): 1}, {"s": {1, 2}}, {"b": b"x"}, object(), "\ud800"],
    ids=["int-key", "tuple-key", "set", "bytes", "object", "lone-surrogate"],
)
def test_non_json_values_are_rejected(bad: object) -> None:
    with pytest.raises(NonCanonicalValueError):
        canonical_json(bad)


def test_deep_nesting_is_a_structured_error_not_a_crash() -> None:
    value: object = 0
    for _ in range(200_000):
        value = [value]
    with pytest.raises(NonCanonicalValueError, match="nested too deeply"):
        canonical_json(value)


@given(json_values)
def test_canonical_json_is_a_fixed_point(value: object) -> None:
    text = canonical_json(value)
    assert canonical_json(json.loads(text)) == text


@given(st.dictionaries(st.text(max_size=6), st.integers(), max_size=8), st.randoms())
def test_key_insertion_order_does_not_matter(mapping: dict[str, int], rnd: object) -> None:
    items = list(mapping.items())
    rnd.shuffle(items)  # type: ignore[attr-defined]  # hypothesis Random
    assert canonical_json(dict(items)) == canonical_json(mapping)


@given(json_values)
def test_freeze_thaw_roundtrip(value: object) -> None:
    frozen = freeze(value)  # type: ignore[arg-type]  # strategy yields JSON values
    assert thaw(frozen) == value
    hash(frozen)  # frozen values are hashable


def test_frozen_map_behaves_like_a_sorted_mapping() -> None:
    fm = FrozenMap.of({"b": 1, "a": (True, None)})
    assert list(fm) == ["a", "b"]
    assert fm["a"] == (True, None)
    assert len(fm) == 2
    assert dict(fm) == {"a": (True, None), "b": 1}
    with pytest.raises(KeyError):
        fm["missing"]


@pytest.mark.parametrize(
    "entries",
    [(("b", 1), ("a", 2)), (("a", 1), ("a", 2)), (("a", [1]),), (("a", 1.5),)],
    ids=["unsorted", "duplicate", "list-value", "float-value"],
)
def test_frozen_map_rejects_invalid_entries(entries: tuple[tuple[str, object], ...]) -> None:
    with pytest.raises(ModelValidationError):
        FrozenMap(entries)  # type: ignore[arg-type]  # deliberately invalid


def test_freeze_rejects_floats() -> None:
    with pytest.raises(ModelValidationError):
        freeze({"a": [1.0]})  # type: ignore[list-item]  # deliberately invalid
