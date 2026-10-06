"""Admin endpoints for inspecting and reconciling pilot egress state."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import Body
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from pydantic import BaseModel, ConfigDict, Field

from jvagent.memory.conversation import Conversation
from jvagent.memory.distributed_conversation_lock import conversation_mutation_lock

from .pilot.state import PilotStateError, PilotTaskStore


class DeliveryReconciliationRequest(BaseModel):
    """Operator's explicit resolution of an uncertain pilot response send."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["delivered", "not_delivered"]
    note: str = Field(default="", max_length=1024)


def _actor_id(current_user: Any) -> str:
    """Resolve the audit actor exclusively from authenticated request context."""

    for key in ("id", "user_id", "sub"):
        value = (
            current_user.get(key)
            if isinstance(current_user, dict)
            else getattr(current_user, key, None)
        )
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    raise ValidationError(message="Authenticated admin identity is unavailable")


async def _conversation_for_agent(agent_id: str, conversation_id: str) -> Conversation:
    conversation = await Conversation.get(conversation_id)
    if conversation is None:
        raise ResourceNotFoundError(
            message="Conversation not found",
            details={"conversation_id": conversation_id},
        )
    agent = await conversation.get_agent()
    if agent is None or str(getattr(agent, "id", "")) != agent_id:
        raise ResourceNotFoundError(
            message="Conversation not found for this agent",
            details={"conversation_id": conversation_id},
        )
    return conversation  # type: ignore[return-value]


@endpoint(
    "/api/agents/{agent_id}/conversations/{conversation_id}/pilot/pending-deliveries",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Orchestrator"],
)
async def list_pilot_pending_deliveries(
    agent_id: str, conversation_id: str
) -> dict[str, Any]:
    """List uncertain pilot sends requiring an administrator decision."""

    conversation = await _conversation_for_agent(agent_id, conversation_id)
    try:
        items = PilotTaskStore(conversation).pending_deliveries()
    except PilotStateError as exc:
        raise ValidationError(message=str(exc)) from exc
    return {
        "deliveries": [
            {
                "task_id": handle.id,
                "question": snapshot.question,
                "message_id": snapshot.delivery_message_id,
                "attempt_count": snapshot.delivery_attempt_count,
                "last_attempt_at": (
                    snapshot.delivery_last_attempt_at.isoformat()
                    if snapshot.delivery_last_attempt_at
                    else None
                ),
            }
            for handle, snapshot in items
        ]
    }


@endpoint(
    "/api/agents/{agent_id}/conversations/{conversation_id}/pilot/deliveries/{task_id}/reconcile",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Orchestrator"],
)
async def reconcile_pilot_delivery(
    agent_id: str,
    conversation_id: str,
    task_id: str,
    request: DeliveryReconciliationRequest = Body(...),
    current_user: Optional[Any] = None,
) -> dict[str, Any]:
    """Record an admin's delivered/not-delivered decision for an uncertain send."""

    conversation = await _conversation_for_agent(agent_id, conversation_id)
    actor = _actor_id(current_user)
    try:
        async with conversation_mutation_lock(conversation.id):
            handle, snapshot = await PilotTaskStore(conversation).reconcile_delivery(
                task_id,
                decision=request.decision,
                actor=actor,
                note=request.note,
            )
    except PilotStateError as exc:
        raise ValidationError(message=str(exc)) from exc
    return {
        "task_id": handle.id,
        "decision": request.decision,
        "status": snapshot.status,
        "attempt_count": snapshot.delivery_attempt_count,
        "retry_authorized": snapshot.delivery_retry_authorized,
        "actor": snapshot.delivery_reconciliation_actor,
        "reconciled_at": (
            snapshot.delivery_reconciliation_at.isoformat()
            if snapshot.delivery_reconciliation_at
            else None
        ),
    }
