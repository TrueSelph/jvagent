"""Characterize the pinned SDK gap before retiring its supported callback."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import SpendRecordedEvent

from jvagent.harness.accounting import NativeSpendRecorder
from jvagent.harness.native import build_native_agent, run_native_agent
from jvagent.harness.spend_store import ConversationSpendStore
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.conversation import Conversation
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_pinned_immediate_spend_listener_changes_nonstreaming_protocol() -> None:
    """A future SDK update that removes this restriction enables adapter review."""
    seen: list[Any] = []

    class Listener(AbstractCapability[Any]):
        async def before_run(self, ctx: Any) -> None:
            await ctx.emit(
                SpendRecordedEvent(
                    model="gpt-4.1",
                    usage=RequestUsage(input_tokens=1, output_tokens=1),
                    usd=Decimal("0.001"),
                    priced=True,
                    budgets=(),
                )
            )

        def listens_to(self, event: Any) -> bool:
            return isinstance(event, SpendRecordedEvent)

        async def on_event(self, ctx: Any, *, event: Any) -> None:
            seen.append(event)

    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")]))
    # This same public model works without a listener; it has no streaming API.
    assert (await Agent(model).run("Hello")).output == "ok"
    with pytest.raises(AssertionError, match="stream_function"):
        await Agent(model, capabilities=[Listener()]).run("Hello")
    assert len(seen) == 1 and isinstance(seen[0], SpendRecordedEvent)


@pytest.mark.asyncio
async def test_output_limit_keeps_spend_before_terminal_snapshot(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "answer"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: answer\ndescription: Answer\n---\nAnswer.\n"
    )
    conversation = await Conversation.create(session_id="output-spend")
    interaction = await Interaction.create(
        utterance="Answer", session_id=conversation.session_id
    )
    store = InteractionStepStore(interaction)

    async def allowed(name: str) -> bool:
        return True

    model = FunctionModel(
        lambda messages, info: ModelResponse(
            parts=[TextPart("complete")],
            model_name="gpt-4.1",
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        ),
        model_name="gpt-4.1",
    )
    agent = build_native_agent(
        model,
        skill_directory=directory,
        tools=[],
        access_check=allowed,
        instructions="Answer",
        step_store=store,
        spend_store=ConversationSpendStore(conversation),
        spend_observer=NativeSpendRecorder(interaction, lock=store.lock),
    )
    with pytest.raises(UsageLimitExceeded):
        await run_native_agent(
            agent,
            "Answer",
            conversation_id=conversation.session_id,
            run_id="limited-response",
            max_output_tokens=1,
        )
    receipts = [
        e["data"]
        for e in interaction.observability_metrics
        if e.get("data", {}).get("native_spend_receipt")
    ]
    assert len(receipts) == 1
    assert receipts[0]["native_run_id"] == "limited-response"
    assert receipts[0]["usage"]["total_tokens"] == 15
    assert receipts[0]["cost_record"]["amount"] > 0
    conversation_spent = [
        value["spent"]
        for key, value in conversation.native_spend.items()
        if "|conversation|" in key
    ]
    assert len(conversation_spent) == 1
    assert float(conversation_spent[0]["usd"]) == pytest.approx(
        receipts[0]["cost_record"]["amount"]
    )
    assert receipts[0]["cost_record"]["estimated"] is True
    persisted = (await Interaction.find({"id": interaction.id}))[0]
    assert persisted.usage["total_tokens"] == 15
    assert persisted.usage["unknown_cost_call_count"] == 0


@pytest.mark.asyncio
async def test_exhausted_budget_has_no_provider_attempt_or_unknown_bill(
    tmp_path: Path,
) -> None:
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage
    from pydantic_ai_harness.spend import SpendEntry, SpendLimitExceeded, SpendLimits

    from jvagent.harness.accounting import recover_native_usage
    from jvagent.harness.spend_store import native_budgets

    directory = tmp_path / "answer"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: answer\ndescription: Answer\n---\nAnswer.\n"
    )
    conversation = await Conversation.create(user_id="caller", session_id="exhausted")
    interaction = await Interaction.create(
        utterance="Answer",
        user_id="caller",
        conversation_id=conversation.id,
        session_id=conversation.session_id,
    )
    interaction.native_run = {"run_id": "preflight", "status": "running"}
    model_calls = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        model_calls.append(1)
        raise AssertionError("Budget refusal must happen before provider admission")

    model = FunctionModel(respond, model_name="gpt-4.1")
    spend = ConversationSpendStore(conversation)
    statuses = await SpendLimits(store=spend, budgets=native_budgets("2", ".4")).status(
        RunContext(
            deps=None,
            model=model,
            usage=RunUsage(),
            run_id="preflight",
            conversation_id=conversation.session_id,
        )
    )
    await spend.add_many(
        [
            SpendEntry(
                key=s.key,
                usd=Decimal(".5"),
                ttl=s.budget.ttl,
                token="conservative-reserve",
            )
            for s in statuses
            if s.budget.window == "conversation"
        ]
    )

    async def allowed(name: str) -> bool:
        return True

    store = InteractionStepStore(interaction)
    agent = build_native_agent(
        model,
        skill_directory=directory,
        tools=[],
        access_check=allowed,
        instructions="Answer",
        step_store=store,
        spend_store=spend,
        spend_observer=NativeSpendRecorder(interaction, lock=store.lock),
        max_conversation_cost_usd=".4",
    )
    with pytest.raises(SpendLimitExceeded):
        await run_native_agent(
            agent, "Answer", conversation_id=conversation.session_id, run_id="preflight"
        )
    interaction.native_run["status"] = "failed"
    await recover_native_usage(interaction, run_id="preflight", model="gpt-4.1")
    assert model_calls == []
    assert (
        sum(
            e.kind == "model_request_started"
            for e in await store.list_events(run_id="preflight")
        )
        == 1
    )
    assert interaction.usage["unknown_cost_call_count"] == 0
    assert interaction.usage["model_attempt_count"] == 0

    from jvagent.harness.recovery import history_for_new_turn

    prepared = await history_for_new_turn(
        interaction,
        conversation_id=conversation.id,
        user_id="caller",
        session_id=conversation.session_id,
    )
    assert prepared.messages and prepared.source["frontier"] == "accepted_input"
    from jvagent.harness.accounting import project_native_usage

    for field, value in [
        ("native_run_id", "foreign"),
        ("native_step_index", 999),
        ("native_request_started_at", "foreign"),
        ("native_refusal_origin", "untrusted"),
    ]:
        projected = await project_native_usage(
            interaction, run_id="preflight", model="gpt-4.1"
        )
        refusal = next(
            e
            for e in projected.observability_metrics
            if e["event_type"] == "model_preflight_refusal"
        )
        refusal["data"][field] = value
        await recover_native_usage(projected, run_id="preflight", model="gpt-4.1")
        assert projected.usage["unknown_cost_call_count"] == 1
