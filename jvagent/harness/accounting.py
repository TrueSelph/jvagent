"""Map native response receipts into JV's existing usage reporting."""

import asyncio
import contextlib
import contextvars
import math
from collections.abc import Iterator, Sequence
from typing import Any

from jvspatial import flush_deferred_entities


def _measured_usage(usage: Any, response: Any = None) -> bool:
    """A priced empty/interrupted stream is not proof of complete billing."""
    return (
        usage.has_values()
        and not usage.unmeasured_requests
        and (
            response is None
            or response.state == "complete"
            or response.finish_reason is not None
        )
    )


class NativeSpendRecorder:
    """Persist public spend snapshots before settled message snapshots exist.

    The official run-budget counter gives each billed response a replay-stable
    ordinal, including rejected fallback responses. Share the step-store lock
    when several native children write the same Interaction.

    Upstream gap (pinned Harness 0.66): streamed SpendRecordedEvent listeners
    can miss the final receipt on a limit exception. The supported but deprecated
    on_spend callback is used until immediate failure-safe event delivery exists.
    This adapter records telemetry only; it does not execute or price requests.
    """

    def __init__(self, interaction: Any, *, lock: asyncio.Lock | None = None) -> None:
        self.context: contextvars.ContextVar[Any] = contextvars.ContextVar(
            "native_receipt_context", default=None
        )
        self.interaction = interaction
        self.lock = lock if lock is not None else asyncio.Lock()

    @contextlib.contextmanager
    def bind(self, ctx: Any) -> Iterator[None]:
        token = self.context.set(ctx)
        try:
            yield
        finally:
            self.context.reset(token)

    async def record(self, event: Any) -> None:
        ctx = self.context.get()
        statuses = [status for status in event.budgets if status.budget.window == "run"]
        if len(statuses) != 1 or ctx is None or not ctx.run_id:
            raise ValueError(
                "Native receipts require exactly one run budget and run ID"
            )
        status = statuses[0]
        if status.spent.requests <= 0 or (
            event.priced and (not math.isfinite(float(event.usd)) or event.usd < 0)
        ):
            raise ValueError("Invalid native spend receipt")
        key = f"spend:{status.key}:{status.spent.requests}"
        async with self.lock:
            metrics = list(self.interaction.observability_metrics)
            if any(
                item.get("data", {}).get("native_receipt_key") == key
                for item in metrics
            ):
                # Duplicate in memory does not prove the earlier write committed.
                await self.interaction.save()
                await flush_deferred_entities(self.interaction, strict=True)
                return
            usage = event.usage
            from pydantic_ai.messages import ModelResponse

            response = next(
                (
                    message
                    for message in reversed(getattr(ctx, "messages", ()))
                    if isinstance(message, ModelResponse)
                    and message.run_id == ctx.run_id
                ),
                None,
            )
            measured = _measured_usage(usage, response)
            priced = event.priced and measured
            provider = getattr(getattr(ctx, "model", None), "system", None)
            data = {
                "native_receipt_key": key,
                "native_run_id": ctx.run_id,
                "native_step_index": ctx.run_step,
                "native_spend_receipt": True,
                "model": event.model or "unknown",
                "provider": provider if isinstance(provider, str) else "unknown",
                "native_receipt_origin": "spend_snapshot",
                "usage_status": (
                    "provider_reported"
                    if measured
                    else "provider_partial_or_unreported"
                ),
                "usage": {
                    "prompt_tokens": usage.input_tokens,
                    "completion_tokens": usage.output_tokens,
                    "total_tokens": usage.input_tokens + usage.output_tokens,
                    "cached_prompt_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                },
                "cost_record": {
                    "amount": float(event.usd) if priced else None,
                    "currency": "USD",
                    "source": "pydantic_ai_pricing" if priced else "unpriced",
                    "estimated": bool(priced),
                    "pricing_version": None,
                },
            }
            metrics.append({"event_type": "model_call", "data": data})
            self.interaction.observability_metrics = metrics
            self.interaction.compute_usage()
            await self.interaction.save()
            await flush_deferred_entities(self.interaction, strict=True)


def _spend_receipts(interaction: Any, run_id: str) -> list[dict[str, Any]]:
    return [
        item["data"]
        for item in interaction.observability_metrics
        if item.get("event_type") == "model_call"
        and item.get("data", {}).get("native_run_id") == run_id
        and item.get("data", {}).get("native_spend_receipt")
    ]


def record_native_usage(
    interaction: Any, messages: Sequence[Any], *, run_id: str, model: str
) -> int:
    """Record this run's responses once; library-derived cost remains estimated."""
    from pydantic_ai.messages import ModelResponse

    live = _spend_receipts(interaction, run_id)
    if live:
        # The spend journal includes billed fallback responses absent from final
        # history. Mixing it with snapshot ordinals would double-charge them.
        return len(live)
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
                "cache_write_tokens": usage.cache_write_tokens,
            },
        }
        cost = usage.cost
        if not _measured_usage(usage, message):
            data["usage_status"] = "provider_partial_or_unreported"
            data["cost_record"] = {
                "amount": None,
                "currency": "USD",
                "source": "unpriced",
                "estimated": False,
                "pricing_version": None,
            }
        elif cost is not None and math.isfinite(float(cost)) and cost >= 0:
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
    live = _spend_receipts(interaction, run_id)
    metrics = [
        event
        for event in interaction.observability_metrics
        if event.get("data", {}).get("native_unknown_run") != run_id
        and not (
            event.get("data", {}).get("native_run_id") == run_id
            and event.get("data", {}).get("native_attempt_origin") == "step_events_v1"
        )
    ]
    metrics.extend(
        _request_attempts(
            events,
            run_id=run_id,
            status=interaction.native_run.get("status"),
            live=live,
            receipts=receipts,
        )
    )
    interaction.observability_metrics = metrics
    interaction.compute_usage()


def _request_attempts(
    events: Sequence[Any],
    *,
    run_id: str,
    status: str | None,
    live: list[dict[str, Any]],
    receipts: int,
) -> list[dict[str, Any]]:
    """Project official request boundaries, never schedule or infer HTTP retries.

    Timings include SDK boundary overhead. A missing end is unmeasured, not a
    zero-second request. Fallback HTTP attempts can share one SDK boundary;
    their billing receipts remain independently counted by the spend journal.
    """
    attempts: list[dict[str, Any]] = []
    pending: dict[int, list[tuple[Any, dict[str, Any]]]] = {}
    covered = {item["native_step_index"] for item in live}
    terminal = any(event.kind == "run_failed" for event in events)
    for event in events:
        if event.kind == "model_request_started":
            known = event.step_index in covered if live else len(attempts) < receipts
            covered.discard(event.step_index)
            data = {
                "native_run_id": run_id,
                "native_step_index": event.step_index,
                "native_attempt_origin": "step_events_v1",
                "outcome": (
                    "cancelled"
                    if status in {"cancelled", "delivery_unknown"}
                    else "failed" if terminal or status == "failed" else "incomplete"
                ),
                "usage_status": "receipt_recorded" if known else "provider_unreported",
                "started_at": event.timestamp.isoformat(),
                "duration": None,
                "duration_scope": "sdk_model_request",
            }
            attempts.append({"event_type": "model_attempt", "data": data})
            pending.setdefault(event.step_index, []).append((event, data))
        elif event.kind in {"model_request_completed", "model_request_failed"}:
            queue = pending.get(event.step_index)
            if not queue:
                continue
            start, data = queue.pop(0)
            data["outcome"] = (
                "completed" if event.kind == "model_request_completed" else "failed"
            )
            data["completed_at"] = event.timestamp.isoformat()
            duration = (event.timestamp - start.timestamp).total_seconds()
            if math.isfinite(duration) and duration >= 0:
                data["duration"] = duration
    return attempts
