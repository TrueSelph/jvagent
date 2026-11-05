"""Spend receipts survive snapshot gaps, replay and unpriced provider usage."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic_ai_harness")
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import Budget, BudgetStatus, SpendSnapshot, Spent
from pydantic_ai_harness.step_persistence import StepEvent

from jvagent.harness.accounting import NativeSpendRecorder, recover_native_usage
from jvagent.harness.step_store import InteractionStepStore
from jvagent.memory.interaction import Interaction


def receipt(
    ordinal: int, *, priced: bool = True, missing_usage: bool = False
) -> SpendSnapshot:
    return SpendSnapshot(
        model="gpt-4.1",
        usage=RequestUsage(
            input_tokens=10, output_tokens=5, unmeasured_requests=int(missing_usage)
        ),
        usd=Decimal("0.001") if priced else Decimal(0),
        priced=priced,
        budgets=(
            BudgetStatus(
                budget=Budget(window="run"),
                key="run-bucket",
                spent=Spent(requests=ordinal),
                remaining_usd=None,
                remaining_tokens=None,
                warning=False,
                exhausted=False,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_replayed_spend_and_multiple_receipts_do_not_hide_unanswered_step() -> (
    None
):
    interaction = Interaction(utterance="test")
    await interaction.save()
    recorder = NativeSpendRecorder(interaction)
    with recorder.bind(SimpleNamespace(run_id="test", run_step=1)):
        await recorder.record(receipt(1))
        await recorder.record(receipt(1))
        await recorder.record(receipt(2))
    assert recorder.context.get() is None
    store = InteractionStepStore(interaction)
    for step in (1, 2):
        await store.append_event(
            StepEvent(run_id="test", kind="model_request_started", step_index=step)
        )
    await recover_native_usage(interaction, run_id="test", model="gpt-4.1")
    assert interaction.usage["model_call_count"] == 2
    assert interaction.usage["total_tokens"] == 30
    assert interaction.usage["total_cost_usd"] == pytest.approx(0.002)
    assert interaction.usage["unknown_cost_call_count"] == 1
    await recover_native_usage(interaction, run_id="test", model="gpt-4.1")
    assert interaction.usage["total_cost_usd"] == pytest.approx(0.002)
    assert interaction.usage["unknown_cost_call_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("priced", "missing_usage"), [(False, False), (True, True)])
async def test_unpriced_or_unmeasured_receipt_is_not_free(
    priced: bool, missing_usage: bool
) -> None:
    interaction = Interaction(utterance="test")
    await interaction.save()
    recorder = NativeSpendRecorder(interaction)
    with recorder.bind(SimpleNamespace(run_id="test", run_step=1)):
        await recorder.record(receipt(1, priced=priced, missing_usage=missing_usage))
    assert interaction.usage["model_call_count"] == 1
    assert interaction.usage["unknown_cost_call_count"] == 1
    assert interaction.observability_metrics[0]["data"]["cost_record"]["amount"] is None


@pytest.mark.asyncio
async def test_spend_receipt_without_host_run_scope_is_rejected() -> None:
    interaction = Interaction(utterance="test")
    await interaction.save()
    with pytest.raises(ValueError, match="run ID"):
        await NativeSpendRecorder(interaction).record(receipt(1))
    assert interaction.observability_metrics == []


@pytest.mark.asyncio
async def test_receipt_persists_before_deferred_request_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "true")
    interaction = Interaction(utterance="test")
    await interaction.save()
    recorder = NativeSpendRecorder(interaction)
    with recorder.bind(SimpleNamespace(run_id="test", run_step=1)):
        await recorder.record(receipt(1))
    restored = (await Interaction.find({"id": interaction.id}))[0]
    assert restored.usage["model_call_count"] == 1
    assert restored.usage["total_cost_usd"] == pytest.approx(0.001)
    assert not interaction.deferred_saves_enabled
