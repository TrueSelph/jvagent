"""Persona-backed conversational delivery shared by the gate and ``converse_skill`` tool."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


def format_conversational_directive_for_persona(directive_instructions: str) -> str:
    """Short sub-prompt for Persona ``respond`` mode.

    Utterance and history are already in Persona's scaffolding—only supply concise
    behavioral hints from ``converse_persona_prompt`` (or a single fallback line).
    """
    instr = (directive_instructions or "").strip()
    return instr or "Reply briefly in character; match the user's tone."


async def deliver_conversational_turn(
    action: "AgentInteractAction",
    visitor: "InteractWalker",
    *,
    utterance_override: Optional[str] = None,
) -> str:
    """Run the same path as ``AgentInteractAction._phase_execute_conversational``.

    Returns a short accountability string for tool callers (user-visible reply is
    published via Persona / bus separately).
    """
    conversation = visitor.conversation
    interaction = visitor.interaction
    if not conversation or not interaction:
        logger.warning(
            "deliver_conversational_turn: missing conversation or interaction"
        )
        return "No conversation context; conversational reply was not delivered."

    mode = action._normalize_effective_response_mode(action.response_mode)
    if mode == "respond":
        directive = format_conversational_directive_for_persona(
            action.converse_persona_prompt
        )
        await visitor.add_directive(directive)
        await action.respond(
            visitor,
            use_history=True,
            history_limit=max(
                1, int(getattr(action, "converse_context_limit", 2) or 2)
            ),
        )
        return "Conversational reply delivered via Persona (respond mode)."

    history: List[Dict[str, Any]] = []
    limit = max(0, int(getattr(action, "converse_context_limit", 2) or 0))
    if limit and conversation:
        history = await conversation.get_interaction_history(
            limit=limit,
            excluded=interaction.id,
            with_utterance=True,
            with_response=True,
            formatted=True,
        )
    utterance = (
        utterance_override
        if utterance_override is not None
        else (interaction.utterance or "")
    )
    user_prompt = utterance
    if history:
        lines: List[str] = ["Recent conversation (oldest first):"]
        for h in history:
            u = (h.get("utterance") or h.get("user") or "").strip()
            r = (h.get("response") or h.get("assistant") or "").strip()
            if u:
                lines.append(f"User: {u}")
            if r:
                lines.append(f"Assistant: {r}")
        lines.append(f"\nCurrent user message:\n{utterance}")
        user_prompt = "\n".join(lines)
    await action._deliver_slim_persona_publish(
        visitor, user_prompt=user_prompt, history=[]
    )
    return "Conversational reply delivered via Persona (slim publish)."
