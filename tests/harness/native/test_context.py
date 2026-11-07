"""Library compaction retains context and accounts for its own model requests."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.exceptions import UsageLimitExceeded, UserError
from pydantic_ai.messages import (
    CompactionPart,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import SpendLimitExceeded

from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


def old_history() -> list[Any]:
    return [
        ModelRequest(
            parts=[UserPromptPart("Remember launch code CEDAR-47. " + "old " * 1600)]
        ),
        ModelResponse(parts=[TextPart("Recorded the launch code.")]),
        ModelRequest(parts=[UserPromptPart("Recent filler: " + "later " * 100)]),
        ModelResponse(parts=[TextPart("Noted.")]),
    ]


@pytest.mark.asyncio
async def test_relocated_workspace_history_is_not_silently_rebound(
    tmp_path: Path,
) -> None:
    from copy import deepcopy

    directories = []
    for root in ("original", "relocated"):
        directory = tmp_path / root / "example"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: example\ndescription: Conversation\nallowed-tools: []\n---\nAnswer briefly.\n"
        )
        directories.append(directory)
    calls = 0

    def respond(messages: Any, info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart("ok")])

    async def denied(label: str) -> bool:
        return False

    def build(directory: Path, store: Any = None) -> Any:
        return build_native_agent(
            FunctionModel(respond),
            skill_directory=directory,
            tools=[],
            access_check=denied,
            instructions="Answer briefly.",
            step_store=store,
        )

    result = await run_native_agent(
        build(directories[0]),
        "Remember a fact",
        conversation_id="session",
        run_id="first",
    )
    history = result.all_messages()
    before = deepcopy(history)
    interaction = Interaction()
    store = InteractionStepStore(interaction)
    with pytest.raises(UserError, match="workspace"):
        await run_native_agent(
            build(directories[1], store),
            "Recall",
            conversation_id="session",
            run_id="relocated",
            message_history=history,
        )
    assert calls == 1 and not interaction.native_steps
    assert history == before
    await run_native_agent(
        build(directories[0]),
        "Recall",
        conversation_id="session",
        run_id="retained",
        message_history=history,
    )
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_limit", "budget"), [(1, "20"), (3, "20"), (3, "0.00005")]
)
async def test_summary_is_persisted_charged_and_bound_by_request_limit(
    tmp_path: Path, request_limit: int, budget: str
) -> None:
    directory = tmp_path / "example"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: example\ndescription: Remember facts\nallowed-tools: []\n"
        "---\nAnswer from the conversation.\n"
    )
    interaction = Interaction(utterance="What is the launch code?")
    await interaction.save()
    conversation = Conversation()
    await conversation.save()
    store = InteractionStepStore(interaction)
    seen = []

    async def check(label: str) -> bool:
        raise AssertionError("No Action is needed")

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert info.model_settings["temperature"] == 0.0
        assert info.model_settings["max_tokens"] == 8192
        summary = not info.function_tools
        seen.append("summary" if summary else "answer")
        if summary:
            assert "CEDAR-47" in str(messages)
            assert not info.output_tools
            output = "The user supplied launch code CEDAR-47."
        else:
            assert "Host policy must stay in effect." in info.instructions
            assert "Answer from the conversation." in str(messages)
            assert "old old old" not in str(messages)
            assert any(isinstance(p, CompactionPart) for m in messages for p in m.parts)
            assert "What is the launch code?" in str(messages)
            output = "CEDAR-47"
        return ModelResponse(
            parts=[TextPart(output)],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    agent = build_native_agent(
        FunctionModel(respond, model_name="gpt-4.1"),
        skill_directory=directory,
        tools=[],
        access_check=check,
        instructions="Host policy must stay in effect.",
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        max_conversation_cost_usd=budget,
        spend_observer=NativeSpendRecorder(interaction, lock=store.lock),
        context_max_tokens=800,
        context_keep_tokens=100,
        model_settings={"temperature": 0.0, "max_tokens": 8192},
    )
    arguments = dict(
        conversation_id="session",
        run_id="root",
        message_history=old_history(),
        max_requests=request_limit,
    )
    if budget != "20":
        with pytest.raises(SpendLimitExceeded):
            await run_native_agent(agent, "What is the launch code?", **arguments)
        assert seen == ["summary"]
    elif request_limit == 1:
        with pytest.raises(UsageLimitExceeded):
            await run_native_agent(agent, "What is the launch code?", **arguments)
        # The library reserves one request for the answer before summarizing.
        assert seen == []
    else:
        result = await run_native_agent(agent, "What is the launch code?", **arguments)
        assert result.output == "CEDAR-47"
        assert result.usage.requests == 2
        assert seen == ["summary", "answer"]
        snapshot = await store.latest_snapshot(run_id="root")
        assert snapshot.messages == result.all_messages()
        assert "old old old" not in str(snapshot.messages)
        # The next run uses the compacted graph snapshot again.
        result = await run_native_agent(
            agent,
            "What is the launch code?",
            conversation_id="session",
            run_id="next",
            message_history=snapshot.messages,
        )
        assert result.output == "CEDAR-47"
        assert seen == ["summary", "answer", "answer"]
    await recover_native_usage(interaction, run_id="root", model="gpt-4.1")
    children = await store.list_runs(parent_run_id="root")
    assert len(children) == 1
    assert children[0].agent_name == "summarizing_compaction"
    assert children[0].conversation_id == "session"
    receipts = [
        m["data"]
        for m in interaction.observability_metrics
        if m["event_type"] == "model_call"
    ]
    assert len(receipts) == len(seen)
    if seen:
        assert {m["native_run_id"] for m in receipts} >= {children[0].run_id}
    assert interaction.usage["total_cost_usd"] == pytest.approx(
        sum(m["cost_record"]["amount"] for m in receipts)
    )
    assert interaction.usage["total_tokens"] == 15 * len(seen)
    if seen:
        assert max(
            v["spent"]["requests"] for v in conversation.native_spend.values()
        ) == len(seen)
    else:
        assert not conversation.native_spend
    if request_limit > 1 and budget == "20":
        assert interaction.usage["unknown_cost_call_count"] == 0


@pytest.mark.parametrize(
    "trigger,keep", [(0, 1), (True, 1), (10, -1), (10, 10), (10, 20)]
)
def test_invalid_context_configuration_rejected(trigger: Any, keep: Any) -> None:
    with pytest.raises(ValueError, match="Context"):
        build_native_agent(
            "openai:gpt-4.1",
            skill_directory=Path("missing"),
            tools=[],
            access_check=None,
            instructions="",
            context_max_tokens=trigger,
            context_keep_tokens=keep,
        )


@pytest.mark.asyncio
async def test_failed_summary_never_falls_back_to_full_history(tmp_path: Path) -> None:
    directory = tmp_path / "example"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: example\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
    )
    called = []

    async def access(label: str) -> bool:
        return False

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert not info.function_tools
        called.append("summary")
        raise RuntimeError("summary provider unavailable")

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[],
        access_check=access,
        instructions="Host policy",
        context_max_tokens=800,
        context_keep_tokens=100,
    )
    with pytest.raises(RuntimeError, match="summary provider unavailable"):
        await agent.run("What is the launch code?", message_history=old_history())
    assert called == ["summary"]


@pytest.mark.asyncio
async def test_current_oversized_user_message_is_never_truncated(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "example"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: example\ndescription: Test\nallowed-tools: []\n---\nAnswer.\n"
    )
    prompt = "Keep the whole message. " * 1000 + "FINAL-MARKER"

    async def access(label: str) -> bool:
        return False

    def respond(messages: Any, info: Any) -> ModelResponse:
        assert info.function_tools  # It could not summarize the sole current request.
        assert prompt in str(messages)
        return ModelResponse(parts=[TextPart("received")])

    agent = build_native_agent(
        FunctionModel(respond),
        skill_directory=directory,
        tools=[],
        access_check=access,
        instructions="Host policy",
        context_max_tokens=800,
        context_keep_tokens=100,
    )
    assert (await agent.run(prompt)).output == "received"
