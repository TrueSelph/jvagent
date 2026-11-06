"""Explicit operator reserves for unknown usage; never replay or erase effects."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from jvspatial import flush_deferred_entities

RESERVE_VERSION = "native-usage-reserve-v1"


def recovery_digest(interaction: Any) -> str:
    """Bind an approval to immutable execution facts and original billing."""
    payload = {
        "interaction_id": interaction.id,
        "conversation_id": interaction.conversation_id,
        "user_id": interaction.user_id,
        "session_id": interaction.session_id,
        "run": {
            k: v
            for k, v in interaction.native_run.items()
            if k not in {"usage_reserve", "effect_recovery"}
        },
        "steps": interaction.native_steps,
        "metrics": [
            m
            for m in interaction.observability_metrics
            if m.get("event_type")
            in {"model_call", "model_attempt", "model_preflight_refusal"}
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def has_usage_reserve(interaction: Any) -> bool:
    """Only a durable, fact-bound approval covers the retained unknown count."""
    receipt = interaction.native_run.get("usage_reserve", {})
    try:
        amount = Decimal(receipt["usd"])
        return bool(
            receipt["version"] == RESERVE_VERSION
            and receipt["status"] == "applied"
            and receipt["digest"] == recovery_digest(interaction)
            and receipt["unknown_calls"]
            == interaction.usage.get("unknown_cost_call_count")
            and receipt["unknown_calls"] > 0
            and amount.is_finite()
            and amount > 0
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


async def reserve_unknown_usage(
    interaction: Any,
    conversation: Any,
    *,
    digest: str,
    usd: Decimal,
    actor: str,
    evidence: str,
    reason: str,
    model: Any,
    run_limit: str,
    conversation_limit: str,
) -> dict[str, Any]:
    """Caller holds the host conversation lock and authenticated admin authority.

    Persist intent, apply idempotent public SpendEntries, then acknowledge. A failed
    write leaves admission closed. Reserves are additive estimates, never invoices.
    """
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage
    from pydantic_ai_harness.spend import SpendEntry, SpendLimits

    from jvagent.harness.recovery import NativeHistoryError, history_for_new_turn
    from jvagent.harness.spend_store import ConversationSpendStore, native_budgets

    if (
        not usd.is_finite()
        or usd <= 0
        or usd > Decimal("999999.999999")
        or usd != usd.quantize(Decimal("0.000001"))
        or interaction.native_run.get("status")
        not in {"failed", "cancelled", "running"}
        or not interaction.native_run.get("run_id")
        or not actor
        or not evidence
        or not reason
        or interaction.conversation_id != conversation.id
        or interaction.user_id != conversation.user_id
        or interaction.session_id != conversation.session_id
    ):
        raise ValueError("Invalid uncertainty reserve or conversation scope")
    current = recovery_digest(interaction)
    prior = interaction.native_run.get("usage_reserve")
    accepted_digest = current
    if prior and prior.get("digest") == current:
        accepted_digest = prior.get("approval_digest", current)
    if digest not in {current, accepted_digest}:
        raise ValueError("Execution facts changed; inspect again before approval")
    effect_projection = None
    try:
        if interaction.native_run.get("status") == "running":
            raise NativeHistoryError(
                "Running execution requires stopped-owner verification"
            )
        await history_for_new_turn(
            interaction,
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            session_id=conversation.session_id,
        )
    except NativeHistoryError as error:
        if str(error) != "Previous provider usage requires reconciliation":
            from jvagent.harness.accounting import project_native_usage
            from jvagent.harness.effect_recovery import inspect_effect_recovery

            try:
                # Reserve authority never grants effect replay/continuation. The
                # separate effect approval must reverify the resource afterward.
                await inspect_effect_recovery(interaction, conversation)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                raise ValueError(
                    "Execution/effects are not eligible for a usage reserve"
                ) from error
            effect_projection = await project_native_usage(
                interaction, run_id=interaction.native_run["run_id"], model="unknown"
            )
    approval_digest = prior.get("approval_digest", digest) if prior else digest
    if effect_projection is not None:
        if not effect_projection.usage.get("unknown_cost_call_count"):
            raise ValueError("No unknown provider usage needs a reserve")
        # The explicit POST journals missing observations from SDK facts before
        # budgeting. Original SDK checkpoints/effects/status are never rewritten.
        interaction.observability_metrics = effect_projection.observability_metrics
        interaction.compute_usage()
        await interaction.save()
        await flush_deferred_entities(interaction, strict=True)
        current = recovery_digest(interaction)
    digest = current
    prior = interaction.native_run.get("usage_reserve")
    proposal = {
        "version": RESERVE_VERSION,
        "digest": digest,
        "usd": format(usd.normalize(), "f"),
        "actor": actor,
        "evidence": evidence,
        "reason": reason,
        "unknown_calls": interaction.usage.get("unknown_cost_call_count", 0),
    }
    if prior and "approval_digest" in prior or approval_digest != digest:
        proposal["approval_digest"] = approval_digest
    if prior:
        if any(prior.get(k) != v for k, v in proposal.items()):
            raise ValueError("An existing reserve cannot be replaced")
        if has_usage_reserve(interaction):
            await interaction.save()
            await flush_deferred_entities(interaction, strict=True)
            return prior
    elif proposal["unknown_calls"] <= 0:
        raise ValueError("No unknown provider usage needs a reserve")
    store = ConversationSpendStore(conversation)
    policy = SpendLimits(
        store=store, budgets=native_budgets(run_limit, conversation_limit)
    )
    ctx = RunContext(
        deps=None,
        model=model,
        usage=RunUsage(),
        run_id=interaction.native_run["run_id"],
        conversation_id=conversation.session_id,
    )
    statuses = await policy.status(ctx)
    receipt = {
        **proposal,
        "status": "prepared",
        "created_at": (
            prior["created_at"] if prior else datetime.now(timezone.utc).isoformat()
        ),
    }
    interaction.native_run = {**interaction.native_run, "usage_reserve": receipt}
    await interaction.save()
    await flush_deferred_entities(interaction, strict=True)
    await store.add_many(
        [
            SpendEntry(
                key=status.key,
                usd=usd,
                ttl=status.budget.ttl,
                token=f"{RESERVE_VERSION}:{interaction.id}:{digest}",
            )
            for status in statuses
        ]
    )
    receipt = {**receipt, "status": "applied"}
    interaction.native_run = {**interaction.native_run, "usage_reserve": receipt}
    metrics = [
        m
        for m in interaction.observability_metrics
        if not (
            m.get("event_type") == "model_usage_reserve"
            and m.get("data", {}).get("digest") == digest
        )
    ]
    metrics.append({"event_type": "model_usage_reserve", "data": receipt})
    interaction.observability_metrics = metrics
    interaction.compute_usage()
    await interaction.save()
    await flush_deferred_entities(interaction, strict=True)
    return receipt
