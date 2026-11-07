"""Length-limited replies and summaries must never be accepted as complete."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction
from tests.harness.native.test_context import old_history


@pytest.mark.asyncio
@pytest.mark.parametrize("compact", [False, True])
async def test_length_limited_output_fails_with_its_usage_preserved(
    tmp_path: Path, compact: bool
) -> None:
    skill = tmp_path / "conversation"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: conversation\ndescription: Converse\nallowed-tools: []\n---\nAnswer briefly.\n"
    )
    interaction = Interaction(utterance="Explain the launch code")
    await interaction.save()
    conversation = Conversation()
    await conversation.save()
    store = InteractionStepStore(interaction)
    seen = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        seen.append("summary" if not info.function_tools else "answer")
        return ModelResponse(
            parts=[TextPart("Unfinished content,")],
            finish_reason="length",
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    async def access(label: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=skill,
        tools=[],
        access_check=access,
        instructions="Follow the skill.",
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        spend_observer=NativeSpendRecorder(interaction, lock=store.lock),
        context_max_tokens=800,
        context_keep_tokens=100,
    )
    with pytest.raises(RuntimeError, match="length-limited"):
        await run_native_agent(
            agent,
            "Explain the launch code",
            conversation_id="session",
            run_id="root",
            message_history=old_history() if compact else [],
        )
    await recover_native_usage(interaction, run_id="root", model="gpt-4.1")
    assert seen == (["summary"] if compact else ["answer"])
    assert interaction.usage["model_call_count"] == 1
    assert interaction.usage["total_tokens"] == 15
    receipts = [
        event["data"]["cost_record"]
        for event in interaction.observability_metrics
        if event.get("event_type") == "model_call"
    ]
    assert len(receipts) == 1
    assert receipts[0]["amount"] > 0
    assert interaction.usage["total_cost_usd"] == pytest.approx(receipts[0]["amount"])
    assert interaction.usage["unknown_cost_call_count"] == 0


@pytest.mark.asyncio
async def test_length_limit_stops_output_function_before_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace

    from pydantic_ai.messages import ToolCallPart

    from jvagent.harness import skill_catalog

    skill = tmp_path / "conversation"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: conversation\ndescription: Converse\noutput-contract: evidence_required\nallowed-tools: []\n---\nAnswer briefly.\n"
    )
    called = []

    def finish(value: int) -> str:
        called.append(value)
        return "must not process incomplete output"

    monkeypatch.setattr(
        skill_catalog,
        "output_policy",
        lambda name: SimpleNamespace(finish=finish, run_scope=nullcontext),
    )

    def respond(messages: Any, info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"value": 1})],
            finish_reason="length",
        )

    async def access(label: str) -> bool:
        return True

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=skill,
        tools=[],
        access_check=access,
        instructions="Produce the requested value.",
    )
    with pytest.raises(RuntimeError, match="length-limited"):
        await agent.run("Produce one")
    assert called == []
