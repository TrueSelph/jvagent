"""Native-library boundary regressions; run in the qualified rebuild environment."""

from pathlib import Path
from typing import Any, AsyncIterator

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.native import build_native_agent
from jvagent.tooling.tool import Tool


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_tool_access_and_native_skill_loading(
    tmp_path: Path, authorized: bool
) -> None:
    tmp_path = tmp_path / "example"
    tmp_path.mkdir()
    (tmp_path / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill\n"
        "allowed-tools: [lookup]\n---\nUse lookup to answer.\n"
    )
    called = []
    checked = []
    step = 0

    async def check(name: str) -> bool:
        checked.append(name)
        return authorized

    async def lookup() -> str:
        called.append(True)
        return "receipt"

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            assert {t.name for t in info.function_tools} == {
                "lookup",
                "load_capability",
            }
            assert "Use lookup to answer." in str(messages)
            return ModelResponse(parts=[ToolCallPart("lookup", {})])
        return ModelResponse(parts=[TextPart("done")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=tmp_path,
        tools=[
            Tool("lookup", "Read data", execute=lookup, effect_class="read"),
            Tool("hidden", "Not allowed", effect_class="read"),
        ],
        access_check=check,
        instructions="Follow the selected skill.",
    )
    result = await agent.run("Answer")
    assert result.output == "done"
    assert checked == ["lookup"]
    assert called == ([True] if authorized else [])
    assert "Use lookup to answer." in str(result.all_messages())


@pytest.mark.parametrize("allowed", ["[lookup*]", "lookup", '["lookup?"]'])
def test_ambiguous_tool_policy_rejected(tmp_path: Path, allowed: str) -> None:
    (tmp_path / "SKILL.md").write_text(
        f"---\nname: example\nallowed-tools: {allowed}\n---\nInstructions\n"
    )

    async def check(name: str) -> bool:
        raise AssertionError("Admission must precede access checks")

    with pytest.raises(ValueError):
        build_native_agent(
            FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("x")])),
            skill_directory=tmp_path,
            tools=[],
            access_check=check,
            instructions="Host policy",
        )


@pytest.mark.asyncio
async def test_native_runner_preserves_history_usage_and_events() -> None:
    from pydantic_ai import Agent

    from jvagent.harness.native import run_native_agent

    events = []

    async def collect(ctx: Any, stream: Any) -> None:
        async for event in stream:
            events.append(type(event).__name__)

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        yield "ok"

    agent = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("ok")]),
            stream_function=stream,
        )
    )
    first = await run_native_agent(
        agent,
        "Hello",
        conversation_id="conversation",
        run_id="one",
        event_stream_handler=collect,
    )
    second = await run_native_agent(
        agent,
        "Again",
        conversation_id="conversation",
        run_id="two",
        message_history=first.all_messages(),
    )
    assert second.output == "ok"
    assert len(second.all_messages()) == 4
    assert second.usage.requests == 1
    assert events


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True])
async def test_native_runner_rejects_invalid_limits(limit: Any) -> None:
    from jvagent.harness.native import run_native_agent

    with pytest.raises(ValueError):
        await run_native_agent(
            None,
            "Hello",
            conversation_id="session",
            run_id="run",
            max_requests=limit,
        )


@pytest.mark.asyncio
async def test_output_cap_is_enforced_by_public_sdk() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UsageLimitExceeded
    from pydantic_ai.usage import RequestUsage

    from jvagent.harness.native import run_native_agent

    agent = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(
                parts=[TextPart("answer")],
                usage=RequestUsage(input_tokens=1, output_tokens=30),
            )
        )
    )
    with pytest.raises(UsageLimitExceeded):
        await run_native_agent(
            agent,
            "Answer",
            conversation_id="output-limit",
            run_id="limited",
            max_output_tokens=20,
        )
    result = await run_native_agent(
        agent,
        "Answer",
        conversation_id="output-limit",
        run_id="allowed",
        max_output_tokens=30,
    )
    assert result.output == "answer"
    assert result.usage.output_tokens == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_output_cap_requires_positive_integer(limit: Any) -> None:
    from jvagent.harness.native import run_native_agent

    with pytest.raises(ValueError):
        await run_native_agent(
            None,
            "Answer",
            conversation_id="output-limit",
            run_id="invalid",
            max_output_tokens=limit,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("host_permission", [None, False, True])
async def test_native_explicit_named_permission_requires_host_tool_check(
    tmp_path: Path, host_permission: bool | None
) -> None:
    directory = tmp_path / "named"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: named\ndescription: Named permission\nallowed-tools: [lookup]\n---\nUse lookup.\n"
    )
    called: list[bool] = []
    checked: list[str] = []
    step = 0

    async def access(label: str) -> bool:
        return True

    async def tool_access(tool: Tool) -> bool:
        checked.append(tool.name)
        return bool(host_permission)

    async def lookup() -> str:
        called.append(True)
        return "permitted"

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal step
        step += 1
        return ModelResponse(
            parts=[ToolCallPart("lookup", {})] if step == 1 else [TextPart("done")]
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[
            Tool(
                "lookup",
                "Lookup",
                execute=lookup,
                effect_class="read",
                requires_tool_permission=True,
            )
        ],
        access_check=access,
        tool_access_check=tool_access if host_permission is not None else None,
        instructions="Use the skill.",
    )
    await agent.run("Lookup")
    assert called == ([True] if host_permission else [])
    assert checked == ([] if host_permission is None else ["lookup"])


@pytest.mark.asyncio
async def test_action_argument_named_ctx_does_not_shadow_sdk_context(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "context-argument"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: context-argument\ndescription: Context argument\nallowed-tools: [echo]\n---\nUse echo.\n"
    )
    received = []

    async def echo(ctx: str) -> str:
        received.append(ctx)
        return ctx

    async def allow(name: str) -> bool:
        return True

    def respond(messages: Any, info: Any) -> ModelResponse:
        return (
            ModelResponse(parts=[TextPart("done")])
            if received
            else ModelResponse(parts=[ToolCallPart("echo", {"ctx": "user-data"})])
        )

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[
            Tool(
                "echo",
                "Echo",
                parameters_schema={
                    "type": "object",
                    "properties": {"ctx": {"type": "string"}},
                    "required": ["ctx"],
                    "additionalProperties": False,
                },
                execute=echo,
                effect_class="read",
            )
        ],
        access_check=allow,
        instructions="Echo",
    )
    assert (await agent.run("Echo")).output == "done"
    assert received == ["user-data"]
