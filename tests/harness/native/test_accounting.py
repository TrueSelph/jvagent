"""Failed native runs retain receipts without charging prior-run history twice."""

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
