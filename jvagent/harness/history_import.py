"""Operator-approved text-history import using public SDK messages and spend APIs.

This never replays a legacy run. The protected Conversation receipt freezes only
published user/assistant text and a separately labelled historical spend estimate.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from jvspatial import flush_deferred_entities
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from pydantic_ai_harness.spend import SpendEntry, SpendLimits
from pydantic_ai_harness.step_persistence import is_provider_valid

from jvagent.harness.spend_store import ConversationSpendStore, native_budgets

VERSION = "published-text-import-v1"
MAX_TURNS = 64
MAX_BYTES = 2_000_000


def digest(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("Historical import exceeds bounded snapshot size")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _historical_time(value: Any, naive_timestamp_zone: str | None) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Historical timestamps require reconciliation")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Historical timestamps require reconciliation") from None
    if parsed.tzinfo is None:
        if naive_timestamp_zone != "UTC":
            raise ValueError(
                "Naive historical timestamps require explicit UTC approval"
            )
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _locked(conversation: Any) -> None:
    from jvagent.core.execution_owner import require_native_local_ownership
    from jvagent.memory.distributed_conversation_lock import (
        holds_conversation_mutation_lock,
    )

    require_native_local_ownership()
    if not holds_conversation_mutation_lock(conversation.id):
        raise ValueError("Historical import requires the host conversation lock")


async def inspect_history_import(
    conversation: Any, *, naive_timestamp_zone: str | None = None
) -> dict[str, Any]:
    """Prepare bounded facts from live scoped graph rows; never mutate them."""
    from jvspatial.core.context import get_default_context

    from jvagent.harness.recovery import resolve_history_predecessor
    from jvagent.memory.interaction import Interaction

    _locked(conversation)
    if naive_timestamp_zone not in (None, "UTC"):
        raise ValueError("Only an explicit UTC interpretation is qualified")
    if conversation.channel != "default":
        raise ValueError("Historical channel delivery requires separate migration")
    if conversation.tasks or any(
        k not in {"new_user", "_cost_usd_total", "_cost_accounting_incomplete"}
        for k in conversation.context
    ):
        raise ValueError("Historical task or context state requires separate migration")
    if (
        "new_user" in conversation.context
        and type(conversation.context["new_user"]) is not bool
    ):
        raise ValueError("Historical context metadata is malformed")
    if (
        "_cost_accounting_incomplete" in conversation.context
        and conversation.context["_cost_accounting_incomplete"] is not False
    ):
        raise ValueError("Historical accounting requires separate reconciliation")
    rows = await get_default_context().database.find(
        "node",
        {"entity": "Interaction", "context.conversation_id": conversation.id},
        limit=MAX_TURNS + 33,
    )
    if not rows or len(rows) > MAX_TURNS + 32:
        raise ValueError(
            "Historical interaction set is missing or exceeds migration bounds"
        )
    latest = [r for r in rows if r["id"] == conversation.last_interaction_id]
    if len(latest) != 1:
        raise ValueError("Historical frontier requires reconciliation")
    item = await Interaction.get(latest[0]["id"])
    root, notices = await resolve_history_predecessor(
        item,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        session_id=conversation.session_id,
        allow_legacy_root=True,
        legacy_naive_utc=naive_timestamp_zone == "UTC",
    )
    if root is None or root.native_run:
        raise ValueError("A legacy published-text frontier is required")
    ignored = {n.id for n in notices}
    legacy = [r for r in rows if r["id"] not in ignored]
    if (
        not legacy
        or len(legacy) > MAX_TURNS
        or conversation.interaction_count != len(rows)
    ):
        raise ValueError("Retained history count or bounds require reconciliation")
    messages = []
    source = []
    model_events = tokens = 0
    legacy.sort(
        key=lambda r: (
            _historical_time(r["context"].get("started_at"), naive_timestamp_zone),
            r["id"],
        )
    )
    prior = None
    for row in legacy:
        c = row["context"]
        if any(
            c.get(k) != expected
            for k, expected in {
                "conversation_id": conversation.id,
                "user_id": conversation.user_id,
                "session_id": conversation.session_id,
            }.items()
        ):
            raise ValueError("Historical row belongs to another scope")
        if (
            c.get("native_run")
            or c.get("native_steps")
            or c.get("closed") is not True
            or c.get("emitted") is not True
        ):
            raise ValueError("Historical run or delivery is not settled plain text")
        if any(
            not isinstance(c.get(k), str) or not c[k] for k in ("utterance", "response")
        ):
            raise ValueError("Only published text conversations are supported")
        try:
            started = _historical_time(c["started_at"], naive_timestamp_zone)
            completed = _historical_time(c["completed_at"], naive_timestamp_zone)
            if completed < started or (prior is not None and started < prior):
                raise ValueError("Invalid historical chronology")
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Historical timestamps require reconciliation") from error
        prior = completed
        actions = c.get("actions", [])
        if not isinstance(actions, list) or any(
            not isinstance(a, str)
            or a not in {"OrchestratorInteractAction", "ReplyAction"}
            for a in actions
        ):
            raise ValueError("Historical Actions require effect reconciliation")
        metrics = c.get("observability_metrics", [])
        if not isinstance(metrics, list):
            raise ValueError("Historical metrics require reconciliation")
        for event in metrics:
            if not isinstance(event, dict):
                raise ValueError("Historical metrics require reconciliation")
            kind = event.get("event_type")
            if kind == "model_call":
                data = event.get("data", {})
                if not isinstance(data, dict):
                    raise ValueError("Historical model receipt is malformed")
                model_events += 1
                calls = data.get("tool_calls")
                if calls is not None and not isinstance(calls, list):
                    raise ValueError("Historical tool trace is malformed")
                for call in calls or []:
                    function = call.get("function") if isinstance(call, dict) else None
                    if not isinstance(function, dict) or function.get("name") not in (
                        "reply",
                        "use_skill",
                    ):
                        raise ValueError(
                            "Historical tool effects require separate reconciliation"
                        )
                usage = data.get("usage", {})
                if (
                    isinstance(usage, dict)
                    and all(
                        type(usage.get(k)) is int and usage[k] >= 0
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                    )
                    and usage["total_tokens"]
                    == usage["prompt_tokens"] + usage["completion_tokens"]
                ):
                    tokens += usage["total_tokens"]
            elif kind == "orchestrator_activation":
                data = event.get("data", {})
                if (
                    not isinstance(data, dict)
                    or data.get("ended_via") != "reply"
                    or data.get("flow_owner")
                    or data.get("guards")
                ):
                    raise ValueError(
                        "Historical activation requires separate reconciliation"
                    )
                invoked = data.get("tools_invoked", [])
                if not isinstance(invoked, list) or any(
                    not isinstance(n, str)
                    or n not in {"reply", "use_skill", "(auto-start)"}
                    for n in invoked
                ):
                    raise ValueError(
                        "Historical tool effects require separate reconciliation"
                    )
            elif kind is None and event.get("kind") == "harness.turn_run":
                payload = event.get("payload", {})
                if not isinstance(payload, dict) or payload.get("state") != "completed":
                    raise ValueError("Historical host run requires reconciliation")
                invocations = payload.get("invocations", [])
                if not isinstance(invocations, list) or any(
                    not isinstance(i, dict)
                    or not isinstance(i.get("record"), dict)
                    or i["record"].get("tool_name") not in ("reply", "use_skill")
                    for i in invocations
                ):
                    raise ValueError("Historical host effects require reconciliation")
            elif kind is None and event.get("kind") == "harness.trace":
                spans = event.get("spans", [])
                if not isinstance(spans, list) or any(
                    not isinstance(s, dict)
                    or (
                        "tool_name" in s
                        and s["tool_name"] not in ("reply", "use_skill")
                    )
                    for s in spans
                ):
                    raise ValueError("Historical trace effects require reconciliation")
            else:
                raise ValueError(
                    "Historical attempt or effect trace requires reconciliation"
                )
        messages.extend(
            [
                ModelRequest(parts=[UserPromptPart(c["utterance"], timestamp=started)]),
                ModelResponse(parts=[TextPart(c["response"])], timestamp=completed),
            ]
        )
        source.append(row)
    if not model_events:
        raise ValueError("Historical model events require separate reconciliation")
    if legacy[-1]["id"] != root.id or not is_provider_valid(messages):
        raise ValueError("Historical frontier or typed messages require reconciliation")
    raw_ledger = conversation.context.get("_cost_usd_total")
    if isinstance(raw_ledger, bool):
        raise ValueError("Historical dollar ledger is malformed")
    try:
        ledger = Decimal(str(raw_ledger)) if raw_ledger is not None else None
    except ArithmeticError as error:
        raise ValueError("Historical dollar ledger is malformed") from error
    if ledger is not None and (not ledger.is_finite() or ledger < 0):
        raise ValueError("Historical dollar ledger is malformed")
    frozen = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    facts = {
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "session_id": conversation.session_id,
        "context": conversation.context,
        "naive_timestamp_zone": naive_timestamp_zone,
        "rows": source,
    }
    return {
        "conversation_id": conversation.id,
        "user_id": conversation.user_id,
        "session_id": conversation.session_id,
        "naive_timestamp_zone": naive_timestamp_zone,
        "digest": digest(facts),
        "frontier_id": root.id,
        "operator_frontier_id": conversation.last_interaction_id,
        "interaction_ids": [r["id"] for r in legacy],
        "model_events": model_events,
        "reported_tokens_minimum": tokens,
        "legacy_ledger_usd": str(ledger) if ledger is not None else None,
        "messages": frozen,
        "messages_digest": digest(frozen),
    }


def _public(plan: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "naive_timestamp_zone",
        "conversation_id",
        "user_id",
        "session_id",
        "digest",
        "frontier_id",
        "operator_frontier_id",
        "interaction_ids",
        "model_events",
        "reported_tokens_minimum",
        "legacy_ledger_usd",
        "messages_digest",
        "baseline",
        "actor",
        "evidence",
        "reason",
        "version",
        "status",
        "created_at",
        "budget_key",
        "counter_digest",
        "rollback_actor",
        "rolled_back_at",
    }
    return {k: v for k, v in plan.items() if k in allowed}


def _bound_receipt(conversation: Any, receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Historical import receipt is malformed")
    if any(
        receipt.get(k) != v
        for k, v in {
            "conversation_id": conversation.id,
            "user_id": conversation.user_id,
            "session_id": conversation.session_id,
        }.items()
    ):
        raise ValueError("Historical import belongs to another scope")
    baseline = receipt.get("baseline")
    if (
        receipt.get("version") != VERSION
        or any(
            not isinstance(receipt.get(k), str) or not receipt[k]
            for k in ("actor", "evidence", "reason")
        )
        or not isinstance(baseline, dict)
        or set(baseline) != {"usd", "tokens", "requests", "estimated"}
        or baseline.get("estimated") is not True
        or not isinstance(baseline.get("usd"), str)
        or any(
            type(baseline.get(k)) is not int or baseline[k] < 0
            for k in ("tokens", "requests")
        )
        or not isinstance(receipt.get("digest"), str)
        or len(receipt["digest"]) != 71
        or not receipt["digest"].startswith("sha256:")
        or any(c not in "0123456789abcdef" for c in receipt["digest"][7:])
        or receipt.get("token") != VERSION + ":" + receipt["digest"]
    ):
        raise ValueError("Historical approval metadata requires reconciliation")
    amount = Decimal(baseline["usd"])
    if not amount.is_finite() or amount < 0:
        raise ValueError("Historical approval amount requires reconciliation")


async def _verify_counter(conversation: Any, receipt: dict[str, Any]) -> None:
    _bound_receipt(conversation, receipt)
    key = receipt["budget_key"]
    spent = (await ConversationSpendStore(conversation).get_many([key]))[key]
    baseline = receipt["baseline"]
    if (
        receipt["token"] not in conversation.native_spend.get(key, {}).get("tokens", [])
        or spent.usd < Decimal(baseline["usd"])
        or spent.tokens < baseline["tokens"]
        or spent.requests < baseline["requests"]
        or spent.unpriced_requests < baseline["requests"]
    ):
        raise ValueError("Imported spend baseline requires reconciliation")


async def apply_history_import(
    conversation: Any,
    *,
    source_digest: str,
    usd: Decimal,
    tokens: int,
    requests: int,
    actor: str,
    evidence: str,
    reason: str,
    model: Any,
    run_limit: str,
    conversation_limit: str,
    naive_timestamp_zone: str | None = None,
) -> dict[str, Any]:
    """Prepare intent, add one SDK SpendEntry, acknowledge; retries are idempotent."""
    _locked(conversation)
    proposal = {
        "digest": source_digest,
        "baseline": {
            "usd": str(usd),
            "tokens": tokens,
            "requests": requests,
            "estimated": True,
        },
        "actor": actor,
        "evidence": evidence,
        "reason": reason,
        "naive_timestamp_zone": naive_timestamp_zone,
    }
    if (
        not actor
        or not evidence
        or not reason
        or not isinstance(usd, Decimal)
        or not usd.is_finite()
        or usd < 0
        or any(type(n) is not int or n < 0 for n in (tokens, requests))
    ):
        raise ValueError("A valid operator-approved historical baseline is required")
    prior = conversation.native_history_import
    if prior:
        _bound_receipt(conversation, prior)
        if (
            prior.get("version") != VERSION
            or any(prior.get(k) != v for k, v in proposal.items())
            or prior.get("status") not in {"prepared", "applied"}
        ):
            raise ValueError("Existing historical import cannot be replaced")
        receipt = dict(prior)
        if receipt["status"] == "applied":
            await _verify_counter(conversation, receipt)
            return _public(receipt)
        plan = await inspect_history_import(
            conversation, naive_timestamp_zone=naive_timestamp_zone
        )
        if plan["digest"] != source_digest:
            raise ValueError("Historical source changed before import resumed")
    else:
        if conversation.native_spend:
            raise ValueError(
                "Existing native spending requires separate reconciliation"
            )
        plan = await inspect_history_import(
            conversation, naive_timestamp_zone=naive_timestamp_zone
        )
        ledger = Decimal(plan["legacy_ledger_usd"] or "0")
        if (
            source_digest != plan["digest"]
            or usd < ledger
            or tokens < plan["reported_tokens_minimum"]
            or requests < plan["model_events"]
            or (requests and usd == 0)
        ):
            raise ValueError(
                "Historical baseline or source digest does not cover retained state"
            )
        policy = SpendLimits(
            store=ConversationSpendStore(conversation),
            budgets=native_budgets(run_limit, conversation_limit),
        )
        ctx = RunContext(
            deps=None,
            model=model,
            usage=RunUsage(),
            run_id=VERSION,
            conversation_id=conversation.session_id,
        )
        statuses = [
            s for s in await policy.status(ctx) if s.budget.window == "conversation"
        ]
        if len(statuses) != 1:
            raise ValueError("A single conversation budget is required")
        receipt = {
            **plan,
            **proposal,
            "version": VERSION,
            "status": "prepared",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "budget_key": statuses[0].key,
            "token": VERSION + ":" + source_digest,
        }
        object.__setattr__(conversation, "native_history_import", receipt)
        await conversation.save()
        await flush_deferred_entities(conversation, strict=True)
    await ConversationSpendStore(conversation).add_many(
        [
            SpendEntry(
                key=receipt["budget_key"],
                usd=usd,
                tokens=tokens,
                requests=requests,
                unpriced=requests,
                token=receipt["token"],
            )
        ]
    )
    await _verify_counter(conversation, receipt)
    receipt = {
        **receipt,
        "status": "applied",
        "counter_digest": digest(conversation.native_spend),
    }
    object.__setattr__(conversation, "native_history_import", receipt)
    await conversation.save()
    await flush_deferred_entities(conversation, strict=True)
    return _public(receipt)


async def imported_history(
    conversation: Any, previous: Any
) -> tuple[list[Any], dict[str, Any]]:
    """Use a frozen approved text snapshot, never execute or reprice old tools."""
    receipt = conversation.native_history_import
    _bound_receipt(conversation, receipt)
    if (
        receipt.get("version") != VERSION
        or receipt.get("status") != "applied"
        or receipt.get("frontier_id") != previous.id
        or previous.conversation_id != conversation.id
        or previous.user_id != conversation.user_id
        or previous.session_id != conversation.session_id
    ):
        raise ValueError("Historical text has no matching applied import")
    frozen = receipt.get("messages")
    if digest(frozen) != receipt.get("messages_digest"):
        raise ValueError("Imported historical messages changed")
    messages = ModelMessagesTypeAdapter.validate_python(frozen)
    if not is_provider_valid(messages) or len(messages) != 2 * len(
        receipt["interaction_ids"]
    ):
        raise ValueError("Imported historical messages are malformed")
    canonical = []
    for index, message in enumerate(messages):
        expected = ModelRequest if index % 2 == 0 else ModelResponse
        part = UserPromptPart if index % 2 == 0 else TextPart
        if (
            not isinstance(message, expected)
            or len(message.parts) != 1
            or not isinstance(message.parts[0], part)
        ):
            raise ValueError(
                "Imported history must contain ordinary user/assistant text only"
            )
        content = message.parts[0].content
        if not isinstance(content, str) or not content:
            raise ValueError("Imported history must contain ordinary text only")
        canonical.append(
            ModelRequest(
                parts=[UserPromptPart(content, timestamp=message.parts[0].timestamp)]
            )
            if isinstance(message, ModelRequest)
            else ModelResponse(parts=[TextPart(content)], timestamp=message.timestamp)
        )
    if (
        digest(ModelMessagesTypeAdapter.dump_python(canonical, mode="json"))
        != receipt["messages_digest"]
    ):
        raise ValueError(
            "Imported history contains noncanonical authority or provider fields"
        )
    await _verify_counter(conversation, receipt)
    return messages, {
        "frontier": "imported_published_text",
        "migration_digest": receipt["digest"],
        "historical_spend_estimated": True,
    }


async def rollback_history_import(
    conversation: Any, *, source_digest: str, actor: str
) -> dict[str, Any]:
    """Restore the pre-import empty native counter only before new execution."""
    _locked(conversation)
    receipt = conversation.native_history_import
    _bound_receipt(conversation, receipt)
    if (
        receipt.get("version") == VERSION
        and receipt.get("digest") == source_digest
        and receipt.get("status") == "rolled_back"
        and receipt.get("rollback_actor") == actor
        and conversation.last_interaction_id == receipt.get("operator_frontier_id")
        and conversation.native_spend == {}
    ):
        return _public(receipt)
    if (
        not actor
        or receipt.get("version") != VERSION
        or receipt.get("digest") != source_digest
        or receipt.get("status") != "applied"
        or conversation.last_interaction_id != receipt.get("operator_frontier_id")
        or digest(conversation.native_spend) != receipt.get("counter_digest")
    ):
        raise ValueError(
            "Rollback requires the unchanged pre-execution import frontier"
        )
    conversation.native_spend = {}
    receipt = {
        **receipt,
        "status": "rolled_back",
        "rollback_actor": actor,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
    object.__setattr__(conversation, "native_history_import", receipt)
    await conversation.save()
    await flush_deferred_entities(conversation, strict=True)
    return _public(receipt)
