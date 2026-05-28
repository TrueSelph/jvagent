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
    delivery_intent: str = "engine_output",
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
        delivery_intent: Stylisation flavor for the respond_slim branch.
            ``"engine_output"`` (default) treats ``content`` as a
            pre-composed answer the persona should rephrase in voice.
            ``"smalltalk_emit"`` (Wave 9i.3) treats ``content`` as a
            brief Reflex-generated placeholder and asks persona to
            produce a short in-character greeting/ack matching the
            user's utterance — the placeholder text is hinted to the
            persona but not held as the final answer.
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
        if delivery_intent == "smalltalk_emit":
            # Reflex-generated placeholder. The persona should treat it
            # as a hint — produce a brief greeting/ack in character that
            # matches the user's utterance and tone, NOT rephrase the
            # placeholder verbatim. Single-sentence ceiling keeps the
            # latency win of the EMIT path; "no explanations" prevents
            # the persona from pivoting into a substantive answer that
            # belongs in Reasoning.
            delivery_instruction = (
                "The user just said: '" + (user_utterance or "(empty)") + "'.\n"
                "Reply with a brief in-character greeting, acknowledgment, "
                "or short conversational beat — at most one sentence. "
                "Match the user's tone and language. Do NOT explain what "
                "you can do. Do NOT pivot to a substantive answer. Do NOT "
                "ask a follow-up question unless the user did. A bare "
                "placeholder ack was drafted ('" + text + "'); use it as "
                "a hint, not a verbatim script."
            )
        else:
            delivery_instruction = (
                "You produced the following content in response to the user's "
                "message. Deliver it naturally in your voice — this IS your "
                "answer, not something the user told you. Do not thank the "
                "user for it, do not say 'That's correct'.\n\n"
                "Preserve all SUBSTANTIVE data: product names, specs, prices, "
                "URLs, numeric facts, citations, model identifiers, and "
                "answers to the user's actual question. Reshape phrasing for "
                "natural delivery, but do not drop facts or change numbers.\n\n"
                "STRIP any invitation closer or generic options-menu closer "
                "you find at the end of the drafted text — even if the draft "
                "ended with one, your delivered version must not. Patterns to "
                "remove unconditionally include (non-exhaustive):\n"
                "  - 'Let me know if…' / 'Let me know which…'\n"
                "  - 'Feel free to ask…' / 'Feel free to reach out…'\n"
                "  - 'Anything else I can help with?' / 'Anything specific…?'\n"
                "  - 'Happy to help further' / 'Just say the word'\n"
                "  - 'If you need… let me know' / 'If you'd like…'\n"
                "  - 'Want X or Y?' / 'Would you like X or Y?' / 'Do you "
                "want X or Y?' / 'Need X or Y?'\n"
                "  - 'Should I look up…?' / 'Should I narrow this down…?'\n"
                "  - 'Want more details or a comparison?' / 'Want details or "
                "a recommendation?'\n"
                "  - Any sentence at the tail that offers a menu of next-step "
                "options without naming specific data from THIS response.\n"
                "  - Any closer that would fit a different topic verbatim "
                "(paste-into-another-conversation test).\n\n"
                "End on the substantive answer itself. If the draft's only "
                "closing sentence matches the patterns above, drop it; do "
                "not replace it with a different closer. A forward question "
                "that names specific SKUs, prices, or trade-offs from the "
                "response IS allowed and may be kept — but if you are not "
                "sure it would fail the paste-into-another-conversation "
                "test, drop it.\n\n"
                "Do not add new closers of your own. Silent compliance — "
                "ending cleanly on the answer — beats a templated invitation "
                "every time.\n\n" + text
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
