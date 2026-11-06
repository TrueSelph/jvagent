"""The public SDK callback validates JV declarations before Action I/O."""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.harness.tool_schema import argument_validator
from jvagent.tooling.tool import Tool


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"count": True},
        {"count": "2"},
        {"count": 0},
        {"count": 6},
        {"count": 2, "extra": "private-value"},
    ],
)
def test_declared_types_and_constraints_cannot_be_bypassed(arguments: Any) -> None:
    validate = argument_validator(
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 5}},
            "required": ["count"],
            "additionalProperties": False,
        }
    )
    with pytest.raises(ModelRetry) as caught:
        validate(None, **arguments)
    assert "private-value" not in str(caught.value)


def test_local_refs_work_and_external_refs_never_retrieve() -> None:
    local = argument_validator(
        {
            "type": "object",
            "properties": {"count": {"$ref": "#/$defs/count"}},
            "$defs": {"count": {"type": "integer"}},
        }
    )
    local(None, count=3)
    remote = argument_validator(
        {
            "type": "object",
            "properties": {"count": {"$ref": "http://127.0.0.1:9/private-schema"}},
        }
    )
    with pytest.raises(ModelRetry):
        remote(None, count=3)


@pytest.mark.parametrize(
    "schema",
    [{"type": "private-invalid-type"}, {"$schema": "https://private.invalid/dialect"}],
)
def test_invalid_declarations_fail_without_leaking_values(schema: Any) -> None:
    with pytest.raises(ValueError) as caught:
        argument_validator(schema)
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_schema_retry_precedes_body_and_accepts_nullable_inputs(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "records"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: records\ndescription: Read records\nallowed-tools: [lookup]\n---\nUse lookup.\n"
    )
    called = []

    async def lookup(**values: Any) -> str:
        called.append(values)
        return "verified"

    async def access(name: str) -> bool:
        return True

    step = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup", {"count": "private-invalid-value", "note": None}
                    )
                ]
            )
        if step == 2:
            return ModelResponse(
                parts=[ToolCallPart("lookup", {"count": 2, "note": None})]
            )
        return ModelResponse(parts=[TextPart("done")])

    original = Tool(
        "lookup",
        "Read records",
        {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["count"],
            "additionalProperties": False,
        },
        lookup,
        effect_class="read",
    )
    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=[original],
        access_check=access,
        instructions="Use the actual result.",
    )
    original.parameters_schema["properties"]["count"]["type"] = "boolean"
    assert (await agent.run("Read")).output == "done"
    assert called == [{"count": 2, "note": None}]
