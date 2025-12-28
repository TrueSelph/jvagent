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
    2. Executes when no response has been generated yet, OR when there are
       unexecuted directives (even if a response exists)
    3. Adds a conservative directive and parameters for PersonaAction
       to handle smalltalk while avoiding unsafe knowledge answers

    This ensures that directives furnished by other actions without responses
    are properly executed and result in a generated response.
    """

    directive: str = attribute(
        default=(
            "Only handle smalltalk and casual conversation; politely decline all knowledge-based, "
            "capability-based, and factual engagements by stating that you don't have the "
            "information or ability to respond at the moment."
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

    always_execute: bool = attribute(
        default=True,
        description="Always execute as a last-resort smalltalk fallback regardless of routing.",
    )

    # Default behavioral parameters to enforce / offset the directive
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            # PRIORITY: Knowledge/capability questions - evaluate these first
            {
                "condition": "User asks a knowledge-based question (what, how, why, when, where, who questions about facts, information, or concepts)",
                "response": (
                    "Politely decline and explain that you don't have the information or ability "
                    "to respond at the moment."
                ),
            },
            {
                "condition": "User invokes a capability-based response (can you, do you know, are you able to, tell me about, explain, define)",
                "response": (
                    "Politely decline and explain that you don't have the information or ability "
                    "to respond at the moment."
                ),
            },
            {
                "condition": "User asks a factual question or requests specific information",
                "response": (
                    "Politely decline and explain that you don't have the information or ability "
                    "to respond at the moment."
                ),
            },
            {
                "condition": "User asks about technical details, processes, or how things work",
                "response": (
                    "Politely decline and explain that you don't have the information or ability "
                    "to respond at the moment."
                ),
            },
            # Then handle appropriate smalltalk scenarios
            {
                "condition": "User engages in smalltalk, greetings, or casual conversation",
                "response": (
                    "Respond naturally and conversationally, keeping it brief and friendly."
                ),
            },
            {
                "condition": "The conversation does not warrant a substantive reply",
                "response": (
                    "Politely acknowledge the message but indicate that no specific response is needed, "
                    "or ask how you can help with casual conversation."
                ),
            },
        ],
        description=(
            "Default behavioral parameters for smalltalk fallback. Knowledge question parameters "
            "are ordered first to ensure they are evaluated before smalltalk parameters."
        ),
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute fallback logic when no response exists or unexecuted directives are present.

        This action executes when:
        1. No response has been generated yet (fallback behavior), OR
        2. There are unexecuted directives/parameters (even if a response exists)

        If unexecuted directives/parameters exist from other actions, this action defers
        to them by calling respond() without adding its own directives/parameters.
        Otherwise, it proceeds with its own refined directive and parameters.
        """
        interaction: Interaction | None = visitor.interaction
        if not interaction:
            logger.warning("ConverseInteractAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        try:
            # Check for existing unexecuted directives/parameters
            unexecuted_directives = interaction.get_unexecuted_directives()
            unexecuted_parameters = interaction.get_unexecuted_parameters()
            has_unexecuted = len(unexecuted_directives) > 0 or len(unexecuted_parameters) > 0

            # If unexecuted directives/parameters exist, defer to them
            if has_unexecuted:
                logger.debug(
                    f"ConverseInteractAction: Found {len(unexecuted_directives)} unexecuted directive(s) "
                    f"and {len(unexecuted_parameters)} unexecuted parameter(s); "
                    "deferring to them without adding own directives/parameters"
                )
                # Call respond() without adding our own directives/parameters
                # This allows PersonaAction to execute the existing unexecuted items
                await self.respond(visitor)
                logger.info(
                    "ConverseInteractAction: Executed existing unexecuted directives/parameters"
                )
                return

            # No unexecuted items - check if we should proceed
            # If response already exists and no unexecuted items, skip
            if interaction.has_response():
                logger.debug(
                    "ConverseInteractAction: Interaction already has response and "
                    "no unexecuted items; skipping"
                )
                await visitor.unrecord_action_execution()
                return

            # No response exists - proceed with our refined directive and parameters
            # The refined directive and parameters will ensure PersonaAction does not
            # attempt to respond to knowledge/capability questions
            if not self.directive:
                logger.warning("ConverseInteractAction: Directive not configured, skipping")
                await visitor.unrecord_action_execution()
                return

            await self.respond(
                visitor,
                directives=[self.directive],
                parameters=self.parameters if self.parameters else None,
            )

            logger.info(
                "ConverseInteractAction: Applied fallback smalltalk directive and parameters"
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

