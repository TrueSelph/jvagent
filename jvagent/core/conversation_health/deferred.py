"""Deferred invoke handler for Conversation Health AI Evaluation."""

from __future__ import annotations

import logging
from typing import Any, Dict

from jvspatial import register_deferred_invoke_handler

from .constants import DEFERRED_TASK_TYPE

logger = logging.getLogger(__name__)


async def handle_conversation_health_ai_evaluate(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Run AI evaluation for a single interaction (POST /api/_internal/deferred)."""
    interaction_id = payload.get("interaction_id")
    agent_id = payload.get("agent_id") or ""
    if not interaction_id:
        return {"error": "missing_interaction_id"}

    from .service import run_ai_for_interaction

    return await run_ai_for_interaction(
        str(interaction_id), agent_id=str(agent_id) if agent_id else ""
    )


register_deferred_invoke_handler(
    DEFERRED_TASK_TYPE,
    handle_conversation_health_ai_evaluate,
)
