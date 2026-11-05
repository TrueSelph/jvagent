"""Failed native runs retain receipts without charging prior-run history twice."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import StepEvent

from jvagent.harness.accounting import recover_native_usage
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_native_request_events_restore_attempts_and_duration_once() -> None:
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage
    from pydantic_ai_harness.step_persistence import ContinuableSnapshot

    interaction = await Interaction.create(utterance="request timing")
    interaction.native_run = {"run_id": "timed", "status": "failed"}
    store = InteractionStepStore(interaction)
    start = datetime(2026, 11, 5, tzinfo=timezone.utc)
    for kind, step, seconds in [
        ("model_request_started", 1, 0),
        ("model_request_completed", 1, 2),
        ("model_request_started", 2, 3),
        ("model_request_failed", 2, 7),
    ]:
        await store.append_event(
            StepEvent(
                run_id="timed",
                kind=kind,
                step_index=step,
                timestamp=start + timedelta(seconds=seconds),
            )
        )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="timed",
            step_index=1,
            state="complete",
            messages=[
                ModelResponse(
                    parts=[TextPart("ok")],
                    run_id="timed",
                    usage=RequestUsage(
                        input_tokens=10, output_tokens=5, cost=Decimal("0.001")
                    ),
                )
            ],
        )
    )
    for _ in range(2):
        await recover_native_usage(interaction, run_id="timed", model="test")
        assert interaction.usage["model_attempt_count"] == 2
        assert interaction.usage["failed_model_attempt_count"] == 1
        assert interaction.usage["model_attempt_duration_seconds"] == 6
        assert interaction.usage["total_duration_seconds"] == 2
        assert interaction.usage["model_call_count"] == 1
        assert interaction.usage["total_tokens"] == 15
        assert interaction.usage["total_cost_usd"] == pytest.approx(0.001)
        assert interaction.usage["unknown_cost_call_count"] == 1
    from jvspatial import flush_deferred_entities

    await interaction.save()
    await flush_deferred_entities(interaction, strict=True)
    restored = (await Interaction.find({"id": interaction.id}))[0]
    assert restored.usage["model_attempt_count"] == 2
    assert restored.usage["model_attempt_duration_seconds"] == 6
    assert restored.usage["total_duration_seconds"] == 2


@pytest.mark.asyncio
async def test_completed_request_without_receipt_stays_unknown() -> None:
    interaction = await Interaction.create(utterance="missing billing")
    store = InteractionStepStore(interaction)
    for kind in ("model_request_started", "model_request_completed"):
        await store.append_event(StepEvent(run_id="run", kind=kind, step_index=1))
    await recover_native_usage(interaction, run_id="run", model="test")
    attempts = [
        m["data"]
        for m in interaction.observability_metrics
        if m["event_type"] == "model_attempt"
    ]
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "completed"
    assert interaction.usage["unknown_cost_call_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["missing", "reversed", "cancelled"])
async def test_missing_or_invalid_request_end_is_not_zero_latency(ending: str) -> None:
    interaction = await Interaction.create(utterance="interrupted request")
    interaction.native_run = {"run_id": "run", "status": "cancelled"}
    store = InteractionStepStore(interaction)
    start = datetime(2026, 11, 5, tzinfo=timezone.utc)
    await store.append_event(
        StepEvent(
            run_id="run", kind="model_request_started", step_index=1, timestamp=start
        )
    )
    if ending == "reversed":
        await store.append_event(
            StepEvent(
                run_id="run",
                kind="model_request_completed",
                step_index=1,
                timestamp=start - timedelta(seconds=1),
            )
        )
    if ending == "cancelled":
        await store.append_event(
            StepEvent(
                run_id="run",
                kind="run_failed",
                step_index=1,
                timestamp=start + timedelta(seconds=10),
            )
        )
    await recover_native_usage(interaction, run_id="run", model="test")
    assert interaction.usage["model_attempt_count"] == 1
    assert interaction.usage["unmeasured_model_attempt_count"] == 1
    assert interaction.usage["model_attempt_duration_seconds"] == 0
    assert interaction.usage["unknown_cost_call_count"] == 1
    attempt = interaction.observability_metrics[0]["data"]
    assert attempt["duration"] is None
    assert attempt["outcome"] == ("completed" if ending == "reversed" else "cancelled")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_history", [False, True])
async def test_failed_validation_usage_is_retained_once(with_history: bool) -> None:
    interaction = Interaction(utterance="test")
    await interaction.save()
    prior = await Agent(TestModel(call_tools=[])).run("Earlier", run_id="earlier")
    agent = Agent(
        TestModel(call_tools=[]),
        capabilities=[StepPersistence(store=InteractionStepStore(interaction))],
    )

    @agent.output_validator
    def reject(output: str) -> str:
        raise ModelRetry("Deliberate validation failure")

    with pytest.raises(UnexpectedModelBehavior):
        await agent.run(
            "Test",
            run_id="failed",
            message_history=prior.all_messages() if with_history else (),
        )
    await recover_native_usage(interaction, run_id="failed", model="test")
    assert interaction.usage["model_call_count"] == 2
    assert interaction.usage["total_tokens"] > 0
    for metric in interaction.observability_metrics:
        assert metric["data"]["native_run_id"] == "failed"
    total = interaction.usage["total_tokens"]
    await recover_native_usage(interaction, run_id="failed", model="test")
    assert interaction.usage["total_tokens"] == total
    assert interaction.usage["model_call_count"] == 2


@pytest.mark.asyncio
async def test_missing_provider_receipt_is_unknown_spend() -> None:
    interaction = Interaction(utterance="test")
    await interaction.save()
    await InteractionStepStore(interaction).append_event(
        StepEvent(run_id="missing", kind="model_request_started", step_index=0)
    )
    await recover_native_usage(interaction, run_id="missing", model="test")
    assert interaction.usage["unknown_cost_call_count"] == 1
    await recover_native_usage(interaction, run_id="missing", model="test")
    assert interaction.usage["unknown_cost_call_count"] == 1


@pytest.mark.asyncio
async def test_partial_checkpoint_is_not_a_complete_zero_cost_receipt() -> None:
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage
    from pydantic_ai_harness.step_persistence import ContinuableSnapshot

    interaction = await Interaction.create(utterance="cancelled response")
    interaction.native_run = {"run_id": "partial", "status": "cancelled"}
    store = InteractionStepStore(interaction)
    await store.append_event(
        StepEvent(run_id="partial", kind="model_request_started", step_index=1)
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="partial",
            step_index=1,
            state="interrupted",
            messages=[
                ModelResponse(
                    parts=[TextPart("partial")],
                    run_id="partial",
                    model_name="gpt-4.1",
                    provider_name="openai",
                    state="interrupted",
                    usage=RequestUsage(input_tokens=7, cost=Decimal(0)),
                )
            ],
        )
    )
    await recover_native_usage(interaction, run_id="partial", model="gpt-4.1")
    assert interaction.usage["model_call_count"] == 1
    assert interaction.usage["total_tokens"] == 7
    assert interaction.usage["unknown_cost_call_count"] == 1
    await recover_native_usage(interaction, run_id="partial", model="gpt-4.1")
    assert interaction.usage["model_call_count"] == 1
    assert interaction.usage["unknown_cost_call_count"] == 1
