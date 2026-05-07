"""Per-agent conversation deletion endpoint."""

from __future__ import annotations

from typing import Any, Dict

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.core.agent import Agent


@endpoint(
    "/agents/{agent_id}/conversations/{user_id}/{session_id}",
    methods=["DELETE"],
    auth=True,
    roles=["admin"],
    tags=["Agent"],
    response=success_response(
        data={
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Conversation deleted successfully",
            ),
        }
    ),
)
async def delete_conversation(
    agent_id: str,
    user_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Delete a conversation (and all interactions via cascade) after ownership check."""
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )

    memory = await agent.get_memory()
    if not memory:
        raise ResourceNotFoundError(
            message=f"Memory node not found for agent '{agent_id}'",
            details={"agent_id": agent_id},
        )

    conversation = await memory.get_conversation_by_session(session_id)
    if not conversation:
        raise ResourceNotFoundError(
            message=f"Conversation with session_id '{session_id}' not found",
            details={"session_id": session_id, "agent_id": agent_id},
        )

    if conversation.user_id != user_id:
        raise ValidationError(
            message=(
                f"Conversation with session_id '{session_id}' does not belong to "
                f"user '{user_id}'"
            ),
            details={
                "session_id": session_id,
                "user_id": user_id,
                "conversation_user_id": conversation.user_id,
            },
        )

    # Cascade delete: removes connected Interaction nodes and decrements
    # Memory.total_conversations via Conversation.delete override.
    await conversation.delete(cascade=True)
    return {"message": "Conversation deleted successfully"}
