"""Native Action model calls share the SDK run's budget and receipt graph."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, RequestUsage, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
from jvagent.harness.completion_tool import CompleteText, NativeCompletionTool
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_requests,max_cost,expected_calls",
    [(1, "2.00", 1), (3, "0.0015", 2), (3, "2.00", 3)],
)
async def test_action_completion_shares_usage_and_receipts(
    tmp_path: Path, max_requests: int, max_cost: str, expected_calls: int
) -> None:
    skill = tmp_path / "lookup"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: lookup\ndescription: Read records\n"
        "allowed-tools: [lookup]\n---\nRead records.\n"
    )
    interaction = Interaction(utterance="Find a record")
    conversation = Conversation()
    await interaction.save()
    await conversation.save()
    store = InteractionStepStore(interaction)
    spend = ConversationSpendStore(conversation)
    recorder = NativeSpendRecorder(interaction, lock=store.lock)
    seen = []

    async def lookup(*, complete: CompleteText) -> str:
        return await complete("Rank only these records")

    async def allow(name: str) -> bool:
        return name == "lookup"

    def respond(messages: Any, info: Any) -> ModelResponse:
        if not info.function_tools:
            assert "Rank only these records" in str(messages)
            assert "parent-only context" not in str(messages)
            seen.append("child")
            part = TextPart("record one")
        elif not seen:
            seen.append("parent")
            part = ToolCallPart("lookup", {}, tool_call_id="lookup-1")
        else:
            assert "record one" in str(messages)
            seen.append("parent")
            part = TextPart("Found record one")
        return ModelResponse(
            parts=[part],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=skill,
        tools=[
            NativeCompletionTool(
                "lookup", "Find records", execute=lookup, effect_class="read"
            )
        ],
        access_check=allow,
        instructions="Find the requested record.",
        step_store=store,
        spend_store=spend,
        spend_observer=recorder,
    )

    async def run() -> Any:
        return await run_native_agent(
            agent,
            "Find a record, parent-only context",
            conversation_id="conversation",
            run_id="root",
            max_requests=max_requests,
            max_cost_usd=max_cost,
        )

    if expected_calls < 3:
        with pytest.raises(UsageLimitExceeded):
            await run()
        assert seen == ["parent", "child"][:expected_calls]
    else:
        result = await run()
        assert result.output == "Found record one"
        assert result.usage.requests == 3
        assert seen == ["parent", "child", "parent"]

    records = await store.list_runs(parent_run_id="root")
    assert len(records) == 1 and records[0].conversation_id == "conversation"
    await recover_native_usage(interaction, run_id="root", model="gpt-4.1")
    assert interaction.usage["total_tokens"] == 15 * len(seen)
    assert (
        len([key for key in conversation.native_spend if "|conversation|" in key]) == 1
    )
