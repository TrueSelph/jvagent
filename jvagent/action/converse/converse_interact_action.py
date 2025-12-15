"""ConverseInteractAction as a smalltalk fallback.

This InteractAction runs last (high weight) and only triggers when no other
action has produced a response. It provides a simple, conservative directive
and parameters for PersonaAction to:

- Offer brief, friendly replies to smalltalk and casual conversation
- NEVER attempt to answer knowledge-based or factual questions without
  sufficient certainty about the context
- Politely opt out when a reply is not warranted or outside scope
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.memory.interaction import Interaction


logger = logging.getLogger(__name__)


class ConverseInteractAction(InteractAction):
    """Fallback InteractAction for smalltalk and casual conversation.

    ConverseInteractAction:
    1. Runs last (high weight) as a safety net
    2. Only executes when no response has been generated yet
    3. Adds a conservative directive and parameters for PersonaAction
       to handle smalltalk while avoiding unsafe knowledge answers
    """

    directive: str = attribute(
        default=(
            "Offer a simple, friendly response to smalltalk and casual "
            "conversation. NEVER attempt to answer knowledge-based questions "
            "without having certainty about the context. If the conversation "
            "does not warrant a reply or you lack sufficient information, "
            "politely opt out rather than guessing."
        ),
        description="Fallback directive for smalltalk and conservative replies",
    )

    description: str = attribute(
        default="Fallback interact action for smalltalk and casual conversation.",
        description="Action description",
    )

    # High positive weight so this runs after other InteractActions
    weight: int = attribute(
        default=100,
        description="Execution weight (runs after other InteractActions as a fallback)",
    )

    # Default behavioral parameters to enforce / offset the directive
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "condition": "User asks a knowledge-based or factual question",
                "response": (
                    "Politely decline to answer, explaining that you don't have "
                    "sufficient context or certainty to provide an accurate "
                    "answer. Suggest they check official sources or provide "
                    "more specific context."
                ),
            },
            {
                "condition": "User engages in smalltalk, greetings, or casual conversation",
                "response": (
                    "Respond naturally and conversationally, keeping it brief "
                    "and friendly."
                ),
            },
            {
                "condition": "The conversation does not warrant a substantive reply",
                "response": (
                    "Politely acknowledge the message but indicate that no "
                    "specific response is needed, or ask how you can help."
                ),
            },
            {
                "condition": "User asks about something outside your knowledge or role",
                "response": (
                    "Politely explain that this falls outside your area of "
                    "knowledge or role, and suggest alternative ways they "
                    "might find the information they need."
                ),
            },
        ],
        description=(
            "Default behavioral parameters for smalltalk fallback. These are "
            "forwarded to PersonaAction to enforce conservative behavior."
        ),
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute fallback logic only when no response exists.

        This action should only add guidance when the interaction has no
        response at all. If any response already exists, it opts out and
        unrecords its execution from the action log.
        """
        interaction: Interaction | None = visitor.interaction
        if not interaction:
            logger.warning("ConverseInteractAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        try:
            # If any response already exists, do not run as a fallback
            if interaction.has_response():
                logger.debug(
                    "ConverseInteractAction: Interaction already has response; "
                    "skipping fallback"
                )
                await visitor.unrecord_action_execution()
                return

            # Validate directive is configured
            if not self.directive:
                logger.warning(
                    "ConverseInteractAction: Directive not configured, skipping"
                )
                await visitor.unrecord_action_execution()
                return

            # Generate response via PersonaAction with directive and parameters
            await self.respond(
                visitor,
                directives=[self.directive],
                parameters=self.parameters if self.parameters else None,
            )

            logger.info(
                "ConverseInteractAction: Applied fallback smalltalk directive "
                "and parameters"
            )

        except Exception as e:
            logger.error(
                f"ConverseInteractAction: Error during execution: {e}",
                exc_info=True,
            )
            # Ensure we are not logged as successfully executed
            await visitor.unrecord_action_execution()
            # Do not raise to allow other actions to continue

    async def healthcheck(self) -> bool | dict:
        """Perform health check on the action.

        Validates that the directive is configured. Parameters are optional
        but recommended.
        """
        if not self.directive:
            return {
                "status": False,
                "message": "Directive is not set",
                "severity": "error",
            }

        return True

