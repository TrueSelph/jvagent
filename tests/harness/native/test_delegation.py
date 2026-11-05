"""Native delegation preserves application identity, limits and graph receipts."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from jvagent.tooling.context import bind_dispatch_context, get_dispatch_context
from jvagent.tooling.tool import Tool


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [True, False])
@pytest.mark.parametrize(
    ("request_limit", "max_cost"), [(2, "2.00"), (6, "2.00"), (6, "0.0015")]
)
async def test_native_children_share_limits_and_preserve_scope(
    tmp_path: Path, permitted: bool, request_limit: int, max_cost: str
) -> None:
    child_dir = tmp_path / "reader"
    child_dir.mkdir()
    (child_dir / "SKILL.md").write_text(
        "---\nname: reader\ndescription: Read authorized records\n"
        "allowed-tools: [lookup]\n---\nRead records using lookup.\n"
    )
    interaction = Interaction(utterance="delegate")
    await interaction.save()
    conversation = Conversation()
    await conversation.save()
    store = InteractionStepStore(interaction)
    recorder = NativeSpendRecorder(interaction, lock=store.lock)
    seen = []
    calls = []

    async def lookup() -> str:
        assert get_dispatch_context().user_id == "caller"
        calls.append(True)
        return "authorized record"

    async def access(label: str) -> bool:
        seen.append(label)
        return label == "delegate_task" or permitted

    def respond(messages: Any, info: Any) -> ModelResponse:
        tools = {t.name for t in info.function_tools}
        parent = "delegate_task" in tools
        if parent:
            assert "lookup" not in tools
        else:
            assert tools == {"lookup", "load_capability"}
            assert "parent-only-sensitive-context" not in str(messages)
        tool = "delegate_task" if parent else "lookup"
        has_result = any(
            isinstance(p, ToolReturnPart) and p.tool_name == tool
            for m in messages
            for p in m.parts
        )
        if has_result or (not parent and len(messages) > 2):
            parts = [TextPart("finished")]
        else:
            parts = [
                ToolCallPart(
                    tool,
                    {"agent_name": "reader", "task": "Read records"} if parent else {},
                )
            ]
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=Path("jvagent/skills/delegate"),
        tools=[Tool("lookup", "Read records", execute=lookup, effect_class="read")],
        access_check=access,
        instructions="Follow your selected skill.",
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        spend_observer=recorder,
        conversation_id="host-conversation",
        delegates=[
            {
                "name": "reader",
                "description": "Read records",
                "skill_directory": str(child_dir),
            }
        ],
    )
    visitor = SimpleNamespace(user_id="caller", session_id="host-conversation")
    limited = request_limit == 2 or max_cost == "0.0015"
    with bind_dispatch_context(visitor):
        if limited:
            with pytest.raises(UsageLimitExceeded):
                await run_native_agent(
                    agent,
                    "Delegate parent-only-sensitive-context",
                    conversation_id="host-conversation",
                    run_id="parent",
                    max_requests=request_limit,
                    max_cost_usd=max_cost,
                )
        else:
            result = await run_native_agent(
                agent,
                "Delegate parent-only-sensitive-context",
                conversation_id="host-conversation",
                run_id="parent",
                max_requests=6,
            )
            assert result.output == "finished"
            assert result.usage.requests == 4
    assert recorder.context.get() is None
    await recover_native_usage(interaction, run_id="parent", model="test")
    records = await store.list_runs()
    assert len(records) == 2
    child = next(r for r in records if r.run_id != "parent")
    assert child.parent_run_id == "parent"
    assert child.conversation_id == "host-conversation"
    assert interaction.usage["model_call_count"] == (2 if limited else 4)
    assert interaction.usage["unknown_cost_call_count"] == 0
    expected_requests = 2 if limited else 4
    assert (
        max(item["spent"]["requests"] for item in conversation.native_spend.values())
        == expected_requests
    )
    total = interaction.usage["total_cost_usd"]
    await recover_native_usage(interaction, run_id="parent", model="test")
    assert interaction.usage["total_cost_usd"] == total
    assert calls == ([True] if permitted and max_cost != "0.0015" else [])
    assert seen == (
        ["delegate_task"] if max_cost == "0.0015" else ["delegate_task", "lookup"]
    )


def test_delegate_configuration_rejects_authority_fields() -> None:
    from pydantic import ValidationError

    from jvagent.harness.delegation import NativeDelegate

    with pytest.raises(ValidationError):
        NativeDelegate(
            name="reader", description="Read", skill_directory=".", user_id="other"
        )


@pytest.mark.asyncio
async def test_delegate_cannot_override_host_conversation() -> None:
    from pydantic_ai import Agent

    from jvagent.harness.delegation import ConversationAgent

    agent = ConversationAgent(
        Agent(FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("ok")]))), "host"
    )
    with pytest.raises(ValueError, match="scope cannot change"):
        await agent.run("hello", conversation_id="other")


@pytest.mark.asyncio
async def test_delegation_access_denial_precedes_child_run(tmp_path: Path) -> None:
    child = tmp_path / "reader"
    child.mkdir()
    (child / "SKILL.md").write_text(
        "---\nname: reader\ndescription: Reader\nallowed-tools: []\n---\nRead.\n"
    )
    interaction = Interaction(utterance="deny")
    await interaction.save()
    store = InteractionStepStore(interaction)
    checks = []

    async def denied(label: str) -> bool:
        checks.append(label)
        return False

    def response(messages: Any, info: Any) -> ModelResponse:
        if len(messages) <= 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task", {"agent_name": "reader", "task": "Read"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("denied")])

    agent = build_native_agent(
        FunctionModel(response),
        skill_directory=Path("jvagent/skills/delegate"),
        tools=[],
        access_check=denied,
        instructions="Delegate",
        step_store=store,
        conversation_id="host",
        delegates=[
            {"name": "reader", "description": "Read", "skill_directory": str(child)}
        ],
    )
    result = await run_native_agent(
        agent, "Read", conversation_id="host", run_id="parent"
    )
    assert result.output == "denied"
    assert checks == ["delegate_task"]
    assert [r.run_id for r in await store.list_runs()] == ["parent"]
