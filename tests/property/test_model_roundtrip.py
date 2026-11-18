"""Every model round-trips through to_dict/from_dict and through canonical JSON (SPEC §6)."""

import dataclasses
import importlib
import json
import pkgutil
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import rift.core.models
from rift.core.canonical import canonical_json
from rift.core.serialization import SERIALIZABLE_MODELS, from_dict, to_dict
from tests.strategies import MODEL_STRATEGIES


def _all_model_classes() -> set[type[Any]]:
    """Every dataclass defined in a rift.core.models.* module."""
    found: set[type[Any]] = set()
    for info in pkgutil.iter_modules(rift.core.models.__path__):
        module = importlib.import_module(f"rift.core.models.{info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and dataclasses.is_dataclass(value)
                and value.__module__ == module.__name__
            ):
                found.add(value)
    return found


def test_every_model_is_serializable() -> None:
    assert _all_model_classes() == set(SERIALIZABLE_MODELS)


def test_every_serializable_model_has_a_strategy() -> None:
    assert set(MODEL_STRATEGIES) == set(SERIALIZABLE_MODELS)


_SLOW_OK = settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])


@pytest.mark.parametrize("model_type", SERIALIZABLE_MODELS, ids=lambda t: t.__name__)
@_SLOW_OK
@given(data=st.data())
def test_roundtrip_through_dict(model_type: type[Any], data: st.DataObject) -> None:
    model = data.draw(MODEL_STRATEGIES[model_type])
    assert from_dict(model_type, to_dict(model)) == model


@pytest.mark.parametrize("model_type", SERIALIZABLE_MODELS, ids=lambda t: t.__name__)
@_SLOW_OK
@given(data=st.data())
def test_roundtrip_through_canonical_json(model_type: type[Any], data: st.DataObject) -> None:
    model = data.draw(MODEL_STRATEGIES[model_type])
    text = canonical_json(to_dict(model))
    decoded = from_dict(model_type, json.loads(text))
    assert decoded == model
    # Canonical form is a fixed point: re-encoding the decoded model gives the same bytes.
    assert canonical_json(to_dict(decoded)) == text
