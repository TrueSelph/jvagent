"""Response delivery helpers for CockpitInteractAction (self-contained)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.context import CockpitResult

logger = logging.getLogger(__name__)


async def deliver_conversational(
    action: Any,
    visitor: Any,
    *,
    response_mode: str = "publish",
    converse_persona_prompt: str = "",
    converse_context_limit: int = 2,
) -> None:
    """Deliver a brief conversational reply via PersonaAction.

    Two modes based on response_mode:
    - "respond": add directive, call action.respond()
    - "publish": build prompt from history, call persona.respond_slim()
    """
    interaction = visitor.interaction
    conversation = visitor.conversation
    if not interaction or not conversation:
        return

    mode = _normalize_response_mode(response_mode)

    if mode == "respond":
        directive = (
            converse_persona_prompt.strip()
            or "Reply briefly in character; match the user's tone."
        )
        await visitor.add_directive(directive)
        await action.respond(
            visitor,
            use_history=True,
            history_limit=max(1, converse_context_limit),
        )
        return

    # "publish" mode — slim persona delivery
    persona = await action.get_action("PersonaAction")
    if (
        not persona
        or not getattr(persona, "enabled", True)
        or not hasattr(persona, "respond_slim")
    ):
        logger.warning(
            "deliver_conversational: PersonaAction unavailable, falling back to respond"
        )
        await visitor.add_directive(converse_persona_prompt or "Reply briefly.")
        await action.respond(visitor)
        return

    history: List[Dict[str, Any]] = []
    if converse_context_limit and conversation:
        history = await conversation.get_interaction_history(
            limit=converse_context_limit,
            excluded=interaction.id,
            with_utterance=True,
            with_response=True,
            formatted=True,
        )
    utterance = interaction.utterance or ""
    user_prompt = utterance
    if history:
        lines = ["Recent conversation (oldest first):"]
        for h in history:
            u = (h.get("utterance") or h.get("user") or "").strip()
            r = (h.get("response") or h.get("assistant") or "").strip()
            if u:
                lines.append(f"User: {u}")
            if r:
                lines.append(f"Assistant: {r}")
        lines.append(f"\nCurrent user message:\n{utterance}")
        user_prompt = "\n".join(lines)
    await persona.respond_slim(interaction, visitor, prompt=user_prompt, history=[])


async def deliver_final_response(
    action: Any,
    visitor: Any,
    result: CockpitResult,
    *,
    response_mode: str = "publish",
    degenerate_response_max_chars: int = 25,
    skill_catalog: Optional[SkillCatalog] = None,
) -> None:
    """Deliver the cockpit engine's final response using the appropriate mode.

    Delivery matrix:
    - verbatim (skill catalog override) + not degenerate -> publish raw
    - "respond" + not degenerate -> directive + action.respond()
    - "respond" + degenerate -> publish raw (skip persona for too-short output)
    - degenerate (any mode) -> publish raw
    - default ("publish" + not degenerate) -> persona.respond_slim()
    """
    final_response = result.final_response
    if not final_response or not final_response.strip():
        return

    effective_mode = _resolve_effective_response_mode(
        result, response_mode, skill_catalog
    )
    verbatim = False
    if skill_catalog is not None:
        verbatim = skill_catalog.get_verbatim_final_override(
            set(result.activated_skills)
        )
    degenerate = len(final_response) <= degenerate_response_max_chars

    # Verbatim: publish raw content
    if verbatim and not degenerate:
        await action.publish(visitor, content=final_response, streaming_complete=True)
        return

    # Respond mode with full response
    if effective_mode == "respond" and not degenerate:
        directive = f"Tell the user: {final_response}"
        await visitor.add_directive(directive)
        await action.respond(visitor)
        return

    # Degenerate or publish mode: publish raw
    if degenerate or effective_mode != "respond":
        await action.publish(visitor, content=final_response, streaming_complete=True)
        return

    # Default: slim persona delivery
    persona = await action.get_action("PersonaAction")
    if (
        persona
        and getattr(persona, "enabled", True)
        and hasattr(persona, "respond_slim")
    ):
        interaction = visitor.interaction
        await persona.respond_slim(
            interaction, visitor, prompt=final_response, history=[]
        )
    else:
        await action.publish(visitor, content=final_response, streaming_complete=True)


def _normalize_response_mode(raw_mode: str) -> str:
    mode = (raw_mode or "publish").strip().lower()
    return mode if mode in ("respond", "publish") else "publish"


def _resolve_effective_response_mode(
    result: CockpitResult,
    default_mode: str,
    skill_catalog: Optional[SkillCatalog] = None,
) -> str:
    activated = getattr(result, "activated_skills", None) or []
    if activated and skill_catalog is not None:
        try:
            return skill_catalog.get_response_mode_override(
                set(activated), default_mode
            )
        except Exception:
            pass
    return default_mode
