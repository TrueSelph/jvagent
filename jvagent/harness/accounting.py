"""Map native response receipts into JV's existing usage reporting."""

import math
from collections.abc import Sequence
from typing import Any


def record_native_usage(
    interaction: Any, messages: Sequence[Any], *, run_id: str, model: str
) -> int:
    """Record this run's responses once; library-derived cost remains estimated."""
    from pydantic_ai.messages import ModelResponse

    metrics = list(interaction.observability_metrics)
    known = {event.get("data", {}).get("native_receipt_key") for event in metrics}
    count = 0
    for message in messages:
        if not isinstance(message, ModelResponse) or message.run_id != run_id:
            continue
        count += 1
        key = f"{run_id}:{message.provider_response_id or message.timestamp.isoformat()}:{count}"
        if key in known:
            continue
        usage = message.usage
        data: dict[str, Any] = {
            "native_receipt_key": key,
            "native_run_id": run_id,
            "model": message.model_name or model,
            "provider": message.provider_name or "unknown",
            "usage": {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
                "total_tokens": usage.input_tokens + usage.output_tokens,
                "cached_prompt_tokens": usage.cache_read_tokens,
            },
        }
        cost = usage.cost
        if cost is not None and math.isfinite(float(cost)) and cost >= 0:
            # Native APIs do not consistently distinguish billed cost from the
            # library's price-table calculation. Do not claim actual billing.
            data["cost_record"] = {
                "amount": float(cost),
                "currency": "USD",
                "source": "pydantic_ai_pricing",
                "estimated": True,
                "pricing_version": None,
            }
        metrics.append({"event_type": "model_call", "data": data})
        known.add(key)
    interaction.observability_metrics = metrics
    interaction.compute_usage()
    return count


async def recover_native_usage(interaction: Any, *, run_id: str, model: str) -> None:
    """Settle this run and its recorded descendants without charging other runs."""
    from jvagent.harness.step_store import InteractionStepStore

    store = InteractionStepStore(interaction)
    records = await store.list_runs()
    owned = {run_id}
    while True:
        descendants = {r.run_id for r in records if r.parent_run_id in owned}
        expanded = owned | descendants
        if expanded == owned:
            break
        owned = expanded
    for identity in sorted(owned):
        await _recover_run_usage(interaction, run_id=identity, model=model)


async def _recover_run_usage(interaction: Any, *, run_id: str, model: str) -> None:
    """Read one run's official receipts and retain unmatched request starts."""
    from jvagent.harness.step_store import InteractionStepStore

    store = InteractionStepStore(interaction)
    snapshot = await store.latest_snapshot(run_id=run_id, include_interrupted=True)
    messages = snapshot.messages if snapshot is not None else ()
    receipts = record_native_usage(interaction, messages, run_id=run_id, model=model)
    events = await store.list_events(run_id=run_id)
    attempted = sum(event.kind == "model_request_started" for event in events)
    unknown = max(0, attempted - receipts)
    metrics = [
        event
        for event in interaction.observability_metrics
        if event.get("data", {}).get("native_unknown_run") != run_id
    ]
    metrics.extend(
        {
            "event_type": "model_attempt",
            "data": {
                "outcome": "failed",
                "usage_status": "provider_unreported",
                "native_unknown_run": run_id,
            },
        }
        for _ in range(unknown)
    )
    interaction.observability_metrics = metrics
    interaction.compute_usage()
