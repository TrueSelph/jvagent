"""Canned lead-ins and low-confidence clarification (router response gating)."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any, List, Optional

from jvagent.action.router.routing_result import RoutingResult

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


async def publish_canned_response(
    router: Any, visitor: Any, result: RoutingResult
) -> None:
    """Publish a brief transient canned line when enabled and appropriate."""
    if not router._enable_canned_response:
        return

    if result.intent_type in router._skip_canned_for_intents:
        return

    canned = result.canned_response
    if not canned or not canned.strip():
        return

    interaction = visitor.interaction
    if not interaction:
        return

    if interaction.response:
        return

    try:
        await router._action.publish(visitor, canned.strip(), transient=True)
        interaction.canned_response = canned.strip()
        await interaction.save()
    except Exception as e:
        logger.warning("AgentInteractRouter: Failed to publish canned response: %s", e)


async def evaluate_confidence(
    router: Any,
    result: RoutingResult,
    visitor: Any,
    interaction: "Interaction",
) -> RoutingResult:
    """Optionally publish clarification and mark intent UNCLEAR when confidence is low."""
    if not result.should_clarify(router._confidence_threshold):
        return result

    issues = result.verification.issues_found if result.verification else []
    logger.info(
        "AgentInteractRouter: Low confidence (%.2f < %.2f), issues: %s",
        result.confidence,
        router._confidence_threshold,
        issues,
    )

    if router._enable_clarification:
        clarification = await generate_clarification(
            router,
            interaction.utterance or "",
            result.interpretation,
            result.intent_type,
            result.confidence,
            issues,
            interaction=interaction,
        )
        if clarification:
            try:
                await router._action.publish(visitor, clarification, stream=False)
            except Exception as e:
                logger.warning(
                    "AgentInteractRouter: Failed to publish clarification: %s", e
                )

        result.needs_clarification = True
        result.intent_type = "UNCLEAR"

    return result


async def generate_clarification(
    router: Any,
    utterance: str,
    interpretation: str,
    intent_type: str,
    confidence: float,
    issues: List[str],
    *,
    interaction: Optional["Interaction"] = None,
) -> str:
    """Produce clarification text (LLM or template fallbacks)."""
    issues_text = ", ".join(str(i) for i in issues) if issues else "(none)"

    user_tpl = (router._action.routing_clarification_user_prompt_template or "").strip()
    if user_tpl:
        try:
            model_action = await router._action.get_model_action(purpose="router")
            if model_action:
                primary_prompt = user_tpl.format(
                    utterance=utterance,
                    interpretation=interpretation,
                    intent_type=intent_type,
                    confidence=confidence,
                    issues=issues_text,
                )
                clarification = await model_action.generate(
                    prompt=primary_prompt,
                    temperature=0.7,
                    max_tokens=150,
                    model=router._router_model,
                    calling_action_name=(
                        f"{router._action.get_class_name()}_clarification_primary"
                    ),
                    interaction=interaction,
                )
                if clarification and clarification.strip():
                    return clarification.strip()
        except Exception as e:
            logger.warning(
                "AgentInteractRouter: Primary clarification prompt failed: %s", e
            )

    fallbacks = router._action.routing_clarification_fallback_messages
    if not fallbacks:
        return ""
    template = random.choice(fallbacks)
    try:
        model_action = await router._action.get_model_action(purpose="router")
        if model_action:
            prompt = (
                router._action.routing_clarification_paraphrase_prompt_template.format(
                    utterance=utterance,
                    template=template,
                )
            )
            clarification = await model_action.generate(
                prompt=prompt,
                temperature=0.7,
                max_tokens=100,
                model=router._router_model,
                calling_action_name=f"{router._action.get_class_name()}_clarification",
                interaction=interaction,
            )
            if clarification and clarification.strip():
                return clarification.strip()
    except Exception as e:
        logger.warning("AgentInteractRouter: Paraphrase failed, using template: %s", e)
    return template
