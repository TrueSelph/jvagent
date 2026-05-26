"""Response delivery helpers for CockpitInteractAction.

These are thin shims over the unified ``deliver_via_persona`` entrypoint in
``persona_delivery.py``. Kept here so the historical import paths
(``deliver_conversational``, ``deliver_final_response``) continue to work for
any caller that imports them directly.
"""

from __future__ import annotations

from typing import Any, Optional

from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.context import CockpitResult
from jvagent.action.helm.reasoning.delivery.persona_delivery import deliver_via_persona


async def deliver_conversational(
    action: Any,
    visitor: Any,
    *,
    response_mode: str = "publish",
    converse_persona_prompt: str = "",
    converse_context_limit: int = 2,
) -> None:
    """Deliver a brief conversational reply via PersonaAction.

    Used when the router determines the cockpit engine has no specific work
    (CONVERSATIONAL intent or the ``converse`` skill is the sole route).
    Always goes through ``action.respond()`` after adding a conversational
    directive — single PersonaAction call, no engine round-trip.
    """
    interaction = visitor.interaction
    conversation = visitor.conversation
    if not interaction or not conversation:
        return

    directive = (
        converse_persona_prompt or ""
    ).strip() or "Reply briefly in character; match the user's tone."
    await deliver_via_persona(
        action,
        visitor,
        content=None,
        response_mode="respond",
        directive=directive,
        history_limit=max(1, converse_context_limit),
        use_history=True,
    )


async def deliver_final_response(
    action: Any,
    visitor: Any,
    result: CockpitResult,
    *,
    response_mode: str = "publish",
    degenerate_response_max_chars: int = 25,
    skill_catalog: Optional[SkillCatalog] = None,
) -> None:
    """Deliver the cockpit engine's final response via the unified entrypoint.

    Honors per-skill ``response_mode`` and ``verbatim_final`` overrides via
    the supplied ``skill_catalog``. Degenerate responses (short content)
    skip persona rewording and publish raw.
    """
    final_response = result.final_response
    if not final_response or not final_response.strip():
        return

    await deliver_via_persona(
        action,
        visitor,
        content=final_response,
        response_mode=response_mode,
        degenerate_response_max_chars=degenerate_response_max_chars,
        skill_catalog=skill_catalog,
        cockpit_result=result,
    )


__all__ = ["deliver_conversational", "deliver_final_response"]
