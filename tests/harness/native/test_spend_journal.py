"""Spend receipts survive snapshot gaps, replay and unpriced provider usage."""

from dataclasses import replace
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
@pytest.mark.parametrize("partial_tokens", [0, 7])
async def test_interrupted_spend_is_unknown_even_if_library_prices_zero(
    partial_tokens: int,
) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart

    interaction = await Interaction.create(utterance="cancelled stream")
    event = receipt(1)
    event = replace(
        event, usage=RequestUsage(input_tokens=partial_tokens), usd=Decimal(0)
    )
    response = ModelResponse(
        parts=[TextPart("partial")],
        run_id="test",
        state="interrupted",
        usage=event.usage,
    )
    recorder = NativeSpendRecorder(interaction)
    with recorder.bind(SimpleNamespace(run_id="test", run_step=1, messages=[response])):
        await recorder.record(event)
    assert interaction.usage["unknown_cost_call_count"] == 1
    assert interaction.usage["total_tokens"] == partial_tokens
    assert interaction.observability_metrics[0]["data"]["cost_record"]["amount"] is None


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


@pytest.mark.asyncio
async def test_replayed_receipt_persists_after_failed_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = await Interaction.create(utterance="receipt write retry")
    original_save = Interaction.save
    failures = 1

    async def save(self: Interaction, *args: object, **kwargs: object) -> object:
        nonlocal failures
        if self.id == interaction.id and failures:
            failures -= 1
            raise OSError("unavailable store")
        return await original_save(self, *args, **kwargs)

    monkeypatch.setattr(Interaction, "save", save)
    recorder = NativeSpendRecorder(interaction)
    with recorder.bind(SimpleNamespace(run_id="run", run_step=1)):
        with pytest.raises(OSError):
            await recorder.record(receipt(1))
    persisted = (await Interaction.find({"id": interaction.id}))[0]
    assert not persisted.observability_metrics
    restored_wrapper = NativeSpendRecorder(interaction)
    with restored_wrapper.bind(SimpleNamespace(run_id="run", run_step=1)):
        await restored_wrapper.record(receipt(1))
    persisted = (await Interaction.find({"id": interaction.id}))[0]
    assert len(persisted.observability_metrics) == 1
    assert persisted.usage["model_call_count"] == 1
    assert persisted.usage["total_cost_usd"] == pytest.approx(0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["journal", "snapshot"])
async def test_native_cache_counters_reach_existing_usage_aggregation(
    source: str,
) -> None:
    from dataclasses import replace

    from pydantic_ai.messages import ModelResponse, TextPart

    from jvagent.harness.accounting import record_native_usage

    interaction = Interaction(utterance="cached request")
    usage = RequestUsage(
        input_tokens=10, output_tokens=5, cache_read_tokens=4, cache_write_tokens=2
    )
    if source == "journal":
        recorder = NativeSpendRecorder(interaction)
        with recorder.bind(
            SimpleNamespace(
                run_id="cached", run_step=1, model=SimpleNamespace(system="openai")
            )
        ):
            await recorder.record(replace(receipt(1), usage=usage))
    else:
        record_native_usage(
            interaction,
            [
                ModelResponse(
                    parts=[TextPart("ok")],
                    usage=usage,
                    model_name="gpt-4.1",
                    provider_name="openai",
                    run_id="cached",
                )
            ],
            run_id="cached",
            model="gpt-4.1",
        )
    assert interaction.usage["prompt_tokens"] == 10
    assert interaction.usage["total_tokens"] == 15
    assert interaction.usage["cached_prompt_tokens"] == 4
    assert interaction.usage["cache_write_tokens"] == 2
    assert interaction.observability_metrics[0]["data"]["provider"] == "openai"
