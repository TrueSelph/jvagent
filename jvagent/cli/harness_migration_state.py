"""Offline retained-state preflight; no graph mutation or execution replay."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MAX_STATE_BYTES = 2_000_000
MAX_STATE_INTERACTIONS = 64


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate state snapshot key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("Nonfinite state snapshot constant")


def _amount(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid historical amount")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid historical amount") from error
    if not number.is_finite() or number < 0 or number > Decimal("999999999"):
        raise ValueError("Invalid historical amount")
    return number


def inspect_state_snapshot(path: Path) -> dict[str, Any]:
    """Inspect an operator-controlled snapshot without trusting it as authority.

    Rows use jvspatial's persisted {id, entity, context} envelope. The caller must
    separately verify graph ownership, quiescence, full lifetime accounting and
    capture consistency before any future apply. No report authorizes cutover.
    """
    if not path.is_file():
        raise ValueError("State snapshot must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("State snapshot exceeds preflight size limit")
    try:
        payload = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid state snapshot") from error
    if not isinstance(payload, dict) or set(payload) != {
        "conversation",
        "interactions",
    }:
        raise ValueError(
            "State snapshot requires explicit conversation and interaction rows"
        )
    row = payload["conversation"]
    rows = payload["interactions"]
    if (
        not isinstance(row, dict)
        or row.get("entity") != "Conversation"
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("context"), dict)
        or not isinstance(rows, list)
        or len(rows) > MAX_STATE_INTERACTIONS
    ):
        raise ValueError("Invalid or oversized retained-state snapshot")
    conversation = row["context"]
    if any(
        not isinstance(conversation.get(k), str) or not conversation[k]
        for k in ("user_id", "session_id")
    ):
        raise ValueError("State snapshot requires owner and session scope")
    blockers: set[str] = set()
    ids: set[str] = set()
    reported = Decimal(0)
    estimated = Decimal(0)
    tokens = calls = observed = 0
    tasks = conversation.get("tasks", [])
    if not isinstance(tasks, list) or any(
        not isinstance(task, dict)
        or not isinstance(task.get("status"), str)
        or task.get("status") not in {"completed", "failed", "cancelled"}
        for task in tasks
    ):
        blockers.add("nonterminal_or_malformed_tasks")
    if tasks:
        blockers.add("retained_task_effect_delivery_review")
    if conversation.get("native_spend"):
        blockers.add("existing_native_spend_requires_reconciliation")
    context = conversation.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("Invalid conversation context")
    if set(context) - {"_cost_usd_total", "_cost_accounting_incomplete"}:
        blockers.add("unmapped_conversation_context")
    if (
        "_cost_accounting_incomplete" in context
        and context["_cost_accounting_incomplete"] is not False
    ):
        blockers.add("incomplete_historical_accounting")
    for item in rows:
        if (
            not isinstance(item, dict)
            or item.get("entity") != "Interaction"
            or not isinstance(item.get("context"), dict)
        ):
            raise ValueError("Invalid interaction row")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("Invalid or duplicate interaction identity")
        ids.add(identifier)
        turn = item["context"]
        if any(
            turn.get(k) != expected
            for k, expected in {
                "conversation_id": row["id"],
                "user_id": conversation["user_id"],
                "session_id": conversation["session_id"],
            }.items()
        ):
            blockers.add("foreign_interaction_scope")
        if (
            turn.get("closed") is not True
            or turn.get("emitted") is not True
            or not turn.get("completed_at")
        ):
            blockers.add("unsettled_interaction_or_delivery")
        if turn.get("native_run") or turn.get("native_steps"):
            blockers.add("native_or_mixed_history_requires_reconciliation")
        if any(
            not isinstance(turn.get(k), str) or not turn[k]
            for k in ("utterance", "response")
        ):
            blockers.add("nontext_or_missing_transcript")
        actions = turn.get("actions", [])
        if not isinstance(actions, list) or any(
            not isinstance(action, str)
            or action not in {"OrchestratorInteractAction", "ReplyAction"}
            for action in actions
        ):
            blockers.add("unqualified_action_effects")
        if turn.get("directives") or turn.get("parameters") or turn.get("attachments"):
            blockers.add("retained_tool_or_host_state_review")
        metrics = turn.get("observability_metrics")
        if not isinstance(metrics, list) or not metrics:
            blockers.add("missing_model_receipt_provenance")
            continue
        prior_tokens = tokens
        for event in metrics:
            if (
                not isinstance(event, dict)
                or not isinstance(event.get("event_type"), str)
                or event.get("event_type") not in {"model_call", "embedding_call"}
            ):
                blockers.add("unqualified_attempt_tool_or_reserve_event")
                continue
            observed += 1
            data = event.get("data")
            if not isinstance(data, dict):
                blockers.add("malformed_model_receipt")
                continue
            usage, cost = data.get("usage"), data.get("cost_record")
            if (
                not isinstance(usage, dict)
                or any(
                    type(usage.get(k)) is not int or usage[k] < 0
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                )
                or usage["total_tokens"] <= 0
                or usage["total_tokens"]
                != usage["prompt_tokens"] + usage["completion_tokens"]
                or not isinstance(cost, dict)
                or cost.get("currency") != "USD"
                or type(cost.get("estimated")) is not bool
                or not isinstance(cost.get("source"), str)
                or cost["source"]
                not in {
                    "provider_reported",
                    "litellm_response_cost",
                    "jv_cost_estimator",
                }
                or (
                    cost["source"] == "jv_cost_estimator"
                    and cost["estimated"] is not True
                )
            ):
                blockers.add("missing_or_inconsistent_model_receipt")
                continue
            try:
                amount = _amount(cost.get("amount"))
            except ValueError:
                blockers.add("unknown_or_invalid_historical_cost")
                continue
            if amount == 0 and cost["estimated"]:
                blockers.add("zero_estimate_requires_reconciliation")
                continue
            if cost["estimated"]:
                estimated += amount
            else:
                reported += amount
            tokens += usage["total_tokens"]
            calls += 1
        summary = turn.get("usage", {})
        if summary:
            if (
                not isinstance(summary, dict)
                or type(summary.get("total_tokens")) is not int
                or summary["total_tokens"] != tokens - prior_tokens
                or type(summary.get("model_call_count")) is not int
                or summary["model_call_count"]
                != sum(
                    isinstance(m, dict) and m.get("event_type") == "model_call"
                    for m in metrics
                )
                or type(summary.get("unknown_cost_call_count", 0)) is not int
                or summary.get("unknown_cost_call_count", 0) != 0
            ):
                blockers.add("aggregate_receipts_require_reconciliation")
    if type(conversation.get("interaction_count")) is not int or conversation[
        "interaction_count"
    ] != len(rows):
        blockers.add("retained_interaction_count_mismatch")
    if rows and conversation.get("last_interaction_id") not in ids:
        blockers.add("retained_frontier_mismatch")
    ledger = None
    if "_cost_usd_total" in context:
        try:
            ledger = _amount(context["_cost_usd_total"])
        except ValueError:
            blockers.add("invalid_legacy_conversation_ledger")
    if rows and ledger is None:
        blockers.add("missing_lifetime_spend_baseline")
    if ledger is not None and ledger < reported + estimated:
        blockers.add("legacy_ledger_below_retained_receipts")
    return {
        "schema": "jvagent.retained-state-preflight.v1",
        "status": "blocked" if blockers else "review_required",
        "cutover_authorized": False,
        "snapshot_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "retained_interactions": len(rows),
        "observed_model_events": observed,
        "unverified_model_events": observed - calls,
        "verified_retained_receipts": {
            "requests_including_embeddings": calls,
            "tokens": tokens,
            "reported_usd": str(reported),
            "estimated_usd": str(estimated),
        },
        "legacy_ledger_usd": str(ledger) if ledger is not None else None,
        "legacy_ledger_provenance": "Legacy settlement total; actual/estimated mix not verified",
        "blockers": sorted(blockers),
        "required_before_apply": [
            "Verify live graph ownership and consistent quiescent capture",
            "Reconcile lifetime/pruned usage and receipt provenance",
            "Qualify typed history, shared spend import, idempotent apply and rollback",
            "Resolve all tasks, effects and delivery uncertainty",
        ],
    }
