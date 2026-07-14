"""Deferred invoke handler for Conversation Health AI Evaluation."""

from __future__ import annotations

import logging
from typing import Any, Dict

from jvspatial import register_deferred_invoke_handler

from .constants import DEFERRED_TASK_TYPE

logger = logging.getLogger(__name__)


async def handle_conversation_health_ai_evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run AI evaluation for a single interaction (POST /api/_internal/deferred)."""
    interaction_id = payload.get("interaction_id")
    agent_id = payload.get("agent_id")
    action_id = payload.get("action_id")

    if not interaction_id:
        return {"error": "missing_interaction_id"}

    from .conversation_health_action import ConversationHealthAction

    action = None
    if action_id:
        action = await ConversationHealthAction.get(action_id)
    if not action and agent_id:
        action = await ConversationHealthAction.find_one(
            {
                "context.agent_id": agent_id,
                "context.enabled": True,
            }
        )
    if not action:
        logger.error(
            "ConversationHealthAction not found for AI eval interaction=%s agent=%s",
            interaction_id,
            agent_id,
        )
        return {"error": "action_not_found"}

    return await action.run_ai_for_interaction(str(interaction_id))


register_deferred_invoke_handler(
    DEFERRED_TASK_TYPE,
    handle_conversation_health_ai_evaluate,
)
