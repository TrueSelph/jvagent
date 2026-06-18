"""Tests for jvagent.tooling.signature_schema.

Every schema produced here is also run through the portability validator
(``validate_parameters_schema``) — that validator is the oracle: the deriver's
whole job is to emit only the portable subset.
"""

from __future__ import annotations

import enum
from typing import Annotated, Dict, List, Literal, Optional

import pytest

from jvagent.tooling.signature_schema import (
    build_parameters_schema,
    python_type_to_json_schema,
)
from jvagent.tooling.tool_schema_validator import validate_parameters_schema


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


@pytest.mark.parametrize(
    "annotation, expected",
    [
        (str, {"type": "string"}),
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (bool, {"type": "boolean"}),
        (dict, {"type": "object"}),
        (Dict[str, int], {"type": "object"}),
    ],
)
def test_primitive_mappings(annotation, expected):
    assert python_type_to_json_schema(annotation) == expected


def test_bool_not_treated_as_int():
    # bool is a subclass of int — must resolve to boolean, not integer.
    assert python_type_to_json_schema(bool) == {"type": "boolean"}


def test_list_emits_items():
    assert python_type_to_json_schema(List[str]) == {
        "type": "array",
        "items": {"type": "string"},
    }
    bare = python_type_to_json_schema(list)
    assert bare["type"] == "array"
    assert "items" in bare  # validator requires items even when untyped


def test_optional_unwraps_to_inner():
    assert python_type_to_json_schema(Optional[int]) == {"type": "integer"}


def test_literal_becomes_enum():
    schema = python_type_to_json_schema(Literal["a", "b"])
    assert schema == {"type": "string", "enum": ["a", "b"]}


def test_enum_class_becomes_enum():
    schema = python_type_to_json_schema(Color)
    assert schema == {"type": "string", "enum": ["red", "blue"]}


def test_unknown_type_is_untyped():
    class Weird:
        pass

    assert python_type_to_json_schema(Weird) == {}


# --- build_parameters_schema ------------------------------------------------


def test_required_vs_optional():
    def fn(a: str, b: int = 3, c: Optional[str] = None): ...

    schema = build_parameters_schema(fn)
    assert schema["required"] == ["a"]
    assert schema["properties"]["b"]["default"] == 3
    assert "default" not in schema["properties"]["c"]  # None not echoed
    assert validate_parameters_schema(schema) == []


def test_annotated_description():
    def fn(x: Annotated[int, "how many"]): ...

    schema = build_parameters_schema(fn)
    assert schema["properties"]["x"]["description"] == "how many"
    assert schema["properties"]["x"]["type"] == "integer"
    assert schema["required"] == ["x"]


def test_annotated_optional_combo():
    def fn(x: Annotated[Optional[int], "maybe"] = None): ...

    schema = build_parameters_schema(fn)
    assert schema["properties"]["x"]["type"] == "integer"
    assert schema["properties"]["x"]["description"] == "maybe"
    assert "required" not in schema


def test_self_and_varargs_skipped():
    def fn(self, a: str, *args, **kwargs): ...

    schema = build_parameters_schema(fn)
    assert list(schema["properties"].keys()) == ["a"]


def test_no_params_has_empty_properties():
    def fn(): ...

    schema = build_parameters_schema(fn)
    assert schema == {"type": "object", "properties": {}}
    assert validate_parameters_schema(schema) == []


@pytest.mark.parametrize(
    "fn",
    [
        lambda a, b: None,
        lambda a=1: None,
    ],
)
def test_derived_schema_is_always_portable(fn):
    assert validate_parameters_schema(build_parameters_schema(fn)) == []


def test_complex_signature_is_portable():
    def fn(
        url: Annotated[str, "the url"],
        tags: List[str],
        mode: Literal["fast", "slow"] = "fast",
        limit: Optional[int] = None,
        color: Color = Color.RED,
    ): ...

    schema = build_parameters_schema(fn)
    assert validate_parameters_schema(schema) == []
    assert set(schema["required"]) == {"url", "tags"}
    assert schema["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert schema["properties"]["mode"]["enum"] == ["fast", "slow"]
