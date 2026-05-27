"""Unified persona-delivery entrypoint for the engine.

Replaces the three persona-handoff sites that previously duplicated mode
resolution + verbatim/degenerate handling: ``deliver_conversational``,
``deliver_final_response``, and ``ReasoningHelm._finalize_via_persona``.

Single function. Single mode resolver. Same publish / respond / respond_slim
matrix everywhere.
"""

from __future__ import annotations

from typing import Any, Optional

from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.context import EngineResult


def _normalize_response_mode(raw_mode: str) -> str:
    mode = (raw_mode or "publish").strip().lower()
    return mode if mode in ("respond", "publish") else "publish"


def _resolve_effective_response_mode(
    result: Optional[EngineResult],
    default_mode: str,
    skill_catalog: Optional[SkillCatalog],
) -> str:
    """Resolve the response mode honoring per-skill overrides on the catalog."""
    activated = list(getattr(result, "activated_skills", None) or []) if result else []
    if activated and skill_catalog is not None:
        try:
            return skill_catalog.get_response_mode_override(
                set(activated), default_mode
            )
        except Exception:
            pass
    return _normalize_response_mode(default_mode)


async def deliver_via_persona(
    action: Any,
    visitor: Any,
    *,
    content: Optional[str] = None,
    response_mode: str = "publish",
    directive: Optional[str] = None,
    history_limit: int = 4,
    use_history: bool = True,
    force_raw: bool = False,
    degenerate_response_max_chars: int = 25,
    skill_catalog: Optional[SkillCatalog] = None,
    engine_result: Optional[EngineResult] = None,
) -> None:
    """Single entrypoint for engine → persona response delivery.

    Decision matrix (evaluated in order):

    1. ``force_raw`` and ``content`` set → ``action.publish(content)``.
    2. ``content`` is degenerate (<= ``degenerate_response_max_chars``)
       → ``action.publish(content)`` (skip persona for too-short text).
    3. Effective mode is ``"respond"``:
       - if ``directive`` provided, ``visitor.add_directive(directive)`` first.
       - if ``content`` is set and no directive was given, prepend a
         ``"Tell the user: <content>"`` directive so PersonaAction sees it.
       - then ``action.respond(visitor, ...)``.
    4. Effective mode is ``"publish"`` and ``content`` is set:
       - ``persona.respond_slim(prompt=content)`` if available, else publish raw.

    Args:
        action: The calling action (used for ``publish`` / ``respond``).
        visitor: InteractWalker for response-bus / directives.
        content: Final response text (None for pure-respond delivery driven
            by directives accumulated on the interaction).
        response_mode: ``"publish"`` (default) or ``"respond"``. Per-skill
            overrides from ``skill_catalog`` win when ``engine_result`` is
            supplied with ``activated_skills``.
        directive: Optional directive to add before ``action.respond()``.
            Only used in ``"respond"`` mode.
        history_limit: History depth for ``action.respond(use_history=...)``.
        use_history: Whether ``action.respond()`` should include history.
        force_raw: Skip persona entirely; publish raw content. Used for
            verbatim skill overrides.
        degenerate_response_max_chars: Below this length, persona rewording
            adds no value — publish raw.
        skill_catalog: Optional catalog for response_mode + verbatim_final
            override resolution.
        engine_result: Optional ``EngineResult`` for activated_skills
            (used to resolve overrides). When None, no override is applied.
    """
    text = (content or "").strip()
    effective_mode = _resolve_effective_response_mode(
        engine_result, response_mode, skill_catalog
    )

    verbatim = False
    if engine_result is not None and skill_catalog is not None:
        try:
            verbatim = skill_catalog.get_verbatim_final_override(
                set(getattr(engine_result, "activated_skills", []) or [])
            )
        except Exception:
            verbatim = False

    if force_raw or verbatim:
        if text:
            await action.publish(visitor, content=text, streaming_complete=True)
        return

    is_degenerate = bool(text) and len(text) <= degenerate_response_max_chars
    if is_degenerate:
        await action.publish(visitor, content=text, streaming_complete=True)
        return

    if effective_mode == "respond":
        if directive:
            await visitor.add_directive(directive)
        elif text:
            await visitor.add_directive(f"Tell the user: {text}")
        await action.respond(
            visitor,
            use_history=use_history,
            history_limit=max(1, history_limit),
        )
        return

    # publish mode + non-degenerate content → respond_slim through persona.
    if not text:
        return
    persona = await action.get_action("PersonaAction")
    if (
        persona
        and getattr(persona, "enabled", True)
        and hasattr(persona, "respond_slim")
    ):
        interaction = visitor.interaction
        user_utterance = (interaction.utterance or "").strip() if interaction else ""
        delivery_instruction = (
            "You produced the following content in response to the user's "
            "message. Deliver it naturally in your voice — this IS your "
            "answer, not something the user told you. Do not thank the "
            "user for it, do not say 'That's correct', do not add "
            "invitation closers. Reshape for natural delivery while "
            "preserving all substantive data.\n\n" + text
        )
        await persona.respond_slim(
            interaction,
            visitor,
            prompt=user_utterance or " ",
            extra_system=delivery_instruction,
            history=[],
        )
        return
    await action.publish(visitor, content=text, streaming_complete=True)


__all__ = ["deliver_via_persona"]
