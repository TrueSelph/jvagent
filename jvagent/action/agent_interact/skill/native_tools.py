"""Native AgentInteract tools (always registered; no skill-bundle bootstrap)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from jvagent.action.skill.skill_action_contracts import SkillRunContext
    from jvagent.action.skill.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

NATIVE_CONVERSE_SKILL_NAME = "converse_skill"
NATIVE_SKILL_SEARCH_TOOL = "skill_search"


def register_converse_skill_tool(
    tool_executor: "ToolExecutor",
    ctx: "SkillRunContext",
) -> None:
    """Register ``converse_skill`` — delegates to Persona conversational path."""

    async def converse_skill_handler(args: dict, visitor: Any = None) -> str:
        from jvagent.action.agent_interact.skill.converse_delivery import (
            deliver_conversational_turn,
        )

        state = getattr(ctx, "skill_state", None) or {}
        action = state.get("action")
        walker = state.get("interact_walker")
        if action is None or walker is None:
            return (
                "Error: converse_skill is unavailable (session not wired for "
                "AgentInteract conversational delivery)."
            )
        if state.get("_converse_skill_in_flight"):
            return (
                "Error: converse_skill is not re-entrant within the same agentic loop."
            )
        state["_converse_skill_in_flight"] = True
        try:
            msg = (args or {}).get("message")
            utterance_override = str(msg).strip() if msg is not None else None
            if utterance_override == "":
                utterance_override = None
            return await deliver_conversational_turn(
                action, walker, utterance_override=utterance_override
            )
        except Exception as exc:
            logger.warning("converse_skill: %s", exc, exc_info=True)
            return f"Error: converse_skill failed: {exc}"
        finally:
            state["_converse_skill_in_flight"] = False

    tool_executor.register_dynamic_tool(
        name=NATIVE_CONVERSE_SKILL_NAME,
        tool_def_dict={
            "name": NATIVE_CONVERSE_SKILL_NAME,
            "description": (
                "Deliver a brief conversational reply using the agent persona (same "
                "behavior as the conversational gate). Optional `message` overrides the "
                "utterance taken from the current interaction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Optional user message to respond to.",
                    },
                },
            },
        },
        handler=converse_skill_handler,
    )
