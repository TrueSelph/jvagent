"""Native admission validates declarations without invoking branch hooks."""

from typing import Any

import pytest

from jvagent.action.interview.native import qualify_spec
from jvagent.action.interview.spec import BranchDef, FieldDef, InterviewSpec


@pytest.mark.parametrize(
    "condition",
    [
        {"op": "equals", "value": "premium"},
        {"op": ">=", "value": 10},
        {"op": "in", "value": ["premium", "standard"]},
        {"op": "exists"},
        {"value": "default-equality"},
    ],
)
def test_native_declarative_branch_is_admitted(condition: dict[str, Any]) -> None:
    qualify_spec(
        InterviewSpec(
            name="branch",
            fields=[
                FieldDef(
                    key="choice",
                    prompt="Choice?",
                    branches=[BranchDef(when=condition, goto="detail")],
                ),
                FieldDef(key="detail", prompt="Detail?"),
            ],
        )
    )


@pytest.mark.parametrize(
    "condition",
    [
        {"function": "external_lookup"},
        {"function": ""},
        {"op": "typo", "value": "x"},
        {"op": 42},
        {"op": "equals", "typo": "x"},
        {},
        {"op": "in", "value": "not-a-list"},
        {"op": "equals", "value": float("nan")},
    ],
)
def test_native_invalid_or_hook_branch_is_rejected(condition: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        qualify_spec(
            InterviewSpec(
                name="branch",
                fields=[
                    FieldDef(
                        key="choice",
                        prompt="Choice?",
                        branches=[BranchDef(when=condition, goto="detail")],
                    ),
                    FieldDef(key="detail", prompt="Detail?"),
                ],
            )
        )


@pytest.mark.parametrize("shape", ["cycle", "missing", "duplicate", "empty-target"])
def test_native_ambiguous_or_truncated_paths_are_rejected(shape: str) -> None:
    start = FieldDef(key="choice", prompt="Choice?", else_field="detail")
    end = FieldDef(key="detail", prompt="Detail?")
    if shape == "cycle":
        end.else_field = "choice"
    if shape == "missing":
        start.else_field = "typo"
    if shape == "duplicate":
        end.key = "choice"
    if shape == "empty-target":
        start.else_field = None
        start.branches = [BranchDef(when={"value": "yes"}, goto="")]
    with pytest.raises(ValueError):
        qualify_spec(InterviewSpec(name="branch", fields=[start, end]))
