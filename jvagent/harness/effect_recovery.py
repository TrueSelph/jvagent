"""Operator policy and domain verification around public SDK recovery results.

No tool execution, replay, checkpoint rewriting or effect-state settlement lives
here. Original SDK facts remain intact; an approval authorizes a fresh read-only
turn supplied with a separately verified result through DeferredToolResults.
"""

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from jvspatial import flush_deferred_entities
from pydantic_ai import DeferredToolResults, ToolReturn
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai_harness.step_persistence import fork_run, is_provider_valid
from pydantic_ai_harness.step_persistence.recovery import inspect_recovery

from jvagent.core.execution_owner import require_dead_native_owner
from jvagent.harness.mutation_tools import NativeMutationTool, is_mutation_effect
from jvagent.harness.reconciliation import recovery_digest
from jvagent.harness.step_store import InteractionStepStore

EFFECT_RECOVERY_VERSION = "verified-mutation-recovery-v1"


@dataclass
class EffectInspection:
    facts: dict[str, Any]
    messages: list[ModelMessage]
    deferred: DeferredToolResults | None


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


async def inspect_effect_recovery(
    interaction: Any, conversation: Any
) -> EffectInspection:
    """Caller holds the conversation lock; only a stopped local owner is eligible."""
    from jvagent.harness.accounting import recover_native_usage
    from jvagent.memory.interaction import Interaction

    if (
        interaction.conversation_id != conversation.id
        or interaction.user_id != conversation.user_id
        or interaction.session_id != conversation.session_id
        or interaction.native_run.get("status")
        not in {"running", "failed", "cancelled"}
        or conversation.tasks
    ):
        raise ValueError(
            "Execution scope or domain tasks require separate reconciliation"
        )
    store = InteractionStepStore(interaction)
    runs = await store.list_runs()
    identity = interaction.native_run.get("run_id")
    if (
        len(runs) != 1
        or runs[0].run_id != identity
        or runs[0].parent_run_id
        or runs[0].conversation_id != conversation.session_id
    ):
        raise ValueError("Exactly one scoped root run is required")
    require_dead_native_owner(runs[0].metadata)
    if any(
        runs[0].metadata.get(key) != value
        for key, value in {
            "jv_interaction_id": interaction.id,
            "jv_conversation_id": conversation.id,
            "jv_user_id": conversation.user_id,
            "jv_session_id": conversation.session_id,
        }.items()
    ):
        raise ValueError("The SDK registration is not bound to this graph scope")
    if any(
        not isinstance(item, dict) or item.get("run_id") != identity
        for kind in ("events", "snapshots", "effects")
        for item in interaction.native_steps.get(kind, [])
    ):
        raise ValueError("The execution contains unowned SDK records")
    events = await store.list_events(run_id=identity)
    calls = [e.tool_call_id for e in events if e.kind == "tool_call_started"]
    if not calls or len(calls) != len(set(calls)) or any(not c for c in calls):
        raise ValueError("Tool execution identities are not unambiguous")
    effects = [
        await store.get_tool_effect(run_id=identity, tool_call_id=c) for c in calls
    ]
    if any(e is None for e in effects):
        raise ValueError("Tool execution facts are incomplete")
    mutations = [e for e in effects if is_mutation_effect(e.effect_summary)]
    if len(mutations) != 1:
        raise ValueError("Exactly one reviewed mutation is required")
    effect = mutations[0]
    inspection = await inspect_recovery(store=store, run_id=identity)
    if any(e.tool_call_id != effect.tool_call_id for e in inspection.unresolved):
        raise ValueError("Other tool effects require reconciliation")
    history = await fork_run(store, run_id=identity, include_interrupted=True)
    matching = [
        p
        for m in history
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart) and p.tool_call_id == effect.tool_call_id
    ]
    if len(matching) != 1 or matching[0].tool_name != effect.tool_name:
        raise ValueError("The SDK frontier does not identify the recorded mutation")
    pending = not is_provider_valid(history)
    if pending:
        response_index = len(history) - 1
        if (
            isinstance(history[-1], ModelRequest)
            and not history[-1].parts
            and history[-1].state == "interrupted"
        ):
            response_index -= 1
        response = history[response_index]
        if (
            not isinstance(response, ModelResponse)
            or matching[0] not in response.parts
            or len([p for p in response.parts if isinstance(p, ToolCallPart)]) != 1
            or not is_provider_valid(history[:response_index])
        ):
            raise ValueError("The interrupted SDK frontier is not qualified")
    elif (
        not isinstance(history[-1], ModelRequest)
        or effect.status != "completed"
        or not any(
            isinstance(p, ToolReturnPart) and p.tool_call_id == effect.tool_call_id
            for m in history
            for p in m.parts
        )
    ):
        raise ValueError("Delivery or output validation needs separate reconciliation")
    agent = await conversation.get_agent()
    if agent is None:
        raise ValueError("The owning agent is unavailable")
    if runs[0].metadata.get("jv_agent_id") != agent.id:
        raise ValueError("The SDK registration belongs to another agent")
    declarations: list[NativeMutationTool] = []
    for action in await agent.get_actions(enabled_only=True):
        provider = getattr(action, "get_native_tools", None)
        if provider is None:
            continue
        if action.agent_id != agent.id:
            raise ValueError("Action ownership is inconsistent")
        declarations.extend(
            t
            for t in await provider()
            if isinstance(t, NativeMutationTool)
            and t.name == effect.tool_name
            and t.effect_summary == effect.effect_summary
        )
    if len(declarations) != 1:
        raise ValueError("The mutation has no unique enabled recovery adapter")
    tool = declarations[0]
    scope = await tool.recovery_scope()
    if (
        scope is None
        or effect.idempotency_key != f"{identity}/{effect.tool_call_id}/{scope}"
    ):
        raise ValueError("The recorded mutation resource is unbound or changed")
    try:
        arguments = matching[0].args_as_dict()
    except (ValueError, TypeError):
        raise ValueError("Recorded mutation arguments are malformed") from None
    visitor = SimpleNamespace(
        _agent=agent,
        user_id=conversation.user_id,
        session_id=conversation.session_id,
        conversation=conversation,
        interaction=interaction,
        channel=interaction.channel,
    )
    result = await tool.verify_recovery(visitor=visitor, arguments=arguments)
    if (
        not isinstance(result, ToolReturn)
        or result.content is not None
        or result.tools is not None
    ):
        raise ValueError("Recovery cannot supply extra prompts or tool grants")
    # Projection is isolated: inspection never edits original receipts or usage.
    clone: Any = SimpleNamespace(
        native_steps=copy.deepcopy(interaction.native_steps),
        native_run=copy.deepcopy(interaction.native_run),
        observability_metrics=copy.deepcopy(interaction.observability_metrics),
        usage={},
    )
    clone.compute_usage = lambda: Interaction.compute_usage(clone)
    await recover_native_usage(clone, run_id=identity, model="unknown")
    if clone.usage.get("unknown_cost_call_count"):
        raise ValueError("Provider usage uncertainty requires reconciliation first")
    facts = {
        "version": EFFECT_RECOVERY_VERSION,
        "interaction_id": interaction.id,
        "conversation_id": conversation.id,
        "run_id": identity,
        "tool_name": effect.tool_name,
        "tool_call_id": effect.tool_call_id,
        "effect_summary": effect.effect_summary,
        "resource_scope": scope,
        "source_digest": recovery_digest(interaction),
        "verified_result": result.return_value,
        "verification_metadata": result.metadata,
        "frontier": "interrupted" if pending else "complete",
        "usage": {
            k: clone.usage.get(k)
            for k in (
                "model_call_count",
                "total_tokens",
                "unknown_cost_call_count",
                "estimated_cost_usd",
                "reported_cost_usd",
            )
        },
    }
    facts["digest"] = _digest(facts)
    deferred = (
        DeferredToolResults(calls={effect.tool_call_id: result}) if pending else None
    )
    return EffectInspection(facts, history, deferred)


async def approve_effect_recovery(
    interaction: Any,
    conversation: Any,
    *,
    digest: str,
    actor: str,
    evidence: str,
    reason: str,
) -> dict[str, Any]:
    """Persist intent and approval; never execute or alter the original effect."""
    inspected = await inspect_effect_recovery(interaction, conversation)
    if not actor or not evidence or not reason or digest != inspected.facts["digest"]:
        raise ValueError("Recovery facts changed or approval is incomplete")
    proposal = {
        "version": EFFECT_RECOVERY_VERSION,
        "digest": digest,
        "actor": actor,
        "evidence": evidence,
        "reason": reason,
        "facts": inspected.facts,
    }
    prior = interaction.native_run.get("effect_recovery")
    if prior and any(prior.get(k) != v for k, v in proposal.items()):
        raise ValueError("An existing operator decision cannot be replaced")
    prepared = {
        **proposal,
        "status": "prepared",
        "created_at": (
            prior["created_at"] if prior else datetime.now(timezone.utc).isoformat()
        ),
    }
    if not prior or prior.get("status") != "approved":
        interaction.native_run = {**interaction.native_run, "effect_recovery": prepared}
        await interaction.save()
        await flush_deferred_entities(interaction, strict=True)
    approved = {**prepared, "status": "approved"}
    interaction.native_run = {**interaction.native_run, "effect_recovery": approved}
    try:
        await interaction.save()
        await flush_deferred_entities(interaction, strict=True)
    except BaseException:
        interaction.native_run = {**interaction.native_run, "effect_recovery": prepared}
        raise
    return approved


async def approved_effect_history(
    interaction: Any, conversation: Any
) -> EffectInspection:
    """Recheck resource/version and original facts before every recovery admission."""
    receipt = interaction.native_run.get("effect_recovery", {})
    if (
        receipt.get("status") != "approved"
        or receipt.get("version") != EFFECT_RECOVERY_VERSION
    ):
        raise ValueError("A durable operator approval is required")
    inspected = await inspect_effect_recovery(interaction, conversation)
    if (
        receipt.get("digest") != inspected.facts["digest"]
        or receipt.get("facts") != inspected.facts
    ):
        raise ValueError("Approved recovery facts changed")
    if inspected.deferred is not None:
        for result in inspected.deferred.calls.values():
            result.metadata = {
                **(result.metadata or {}),
                "jv_operator_resolution": {
                    k: receipt[k]
                    for k in (
                        "version",
                        "digest",
                        "actor",
                        "evidence",
                        "reason",
                        "created_at",
                    )
                },
            }
    return inspected
