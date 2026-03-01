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
            "Provide a response to the user message that is aligned with the parameters provided."
        ),
        description="Fallback directive for conservative replies",
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
                "condition": "The user message has diverged from user-specific ACTIVE TASKS (if any).",
                "response": "Respond but in closing, remind the user to return to complete the pending task(s).",
            },
            {
                "condition": "User asks a fact-based or knowledge-based question (what, how, why, when, where, who questions about facts, information, or concepts) and there is no context provided in the directives",
                "response": (
                    "Check your internal knowldge and answer the question to the best of your ability while giving disclaimers that "
                    "it might not be the most acurate or up to date information."
                    "If you cannot answer the question, politely decline and explain that you don't have the information or ability "
                    "to respond at the moment."
                ),
            },
            {
                "condition": "User invokes a capability-based response (can you, do you know, are you able to, tell me about, explain, define) and there is no context provided in the directives",
                "response": (
                    "politely decline and explain that you don't have the information or ability to respond at the moment."
                ),
            },
            # Then handle appropriate smalltalk scenarios
            {
                "condition": "User engages in smalltalk, greetings, or casual conversation",
                "response": (
                    "Respond naturally and conversationally, keeping it brief and aligned with the persona's tone and style."
                ),
            },
            {
                "condition": "The user message does not warrant a substantive reply and there is no context provided in the directives",
                "response": ("Do not respond to the user message."),
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
            has_unexecuted = len(unexecuted_directives) > 0
            has_response = interaction.has_response()
            params_to_pass = self.parameters if self.parameters else None

            # If unexecuted directives exist, defer to them
            if has_unexecuted:
                # Call respond() without adding our own directives
                # This allows PersonaAction to execute the existing unexecuted items
                await self.respond(
                    visitor,
                    parameters=params_to_pass,
                )
                return

            # No unexecuted items - check if we should proceed
            # If response already exists and no unexecuted items, skip
            if has_response:
                logger.debug(
                    "ConverseInteractAction: Interaction already has response and "
                    "no unexecuted items; skipping"
                )
                await visitor.unrecord_action_execution()
                return

            # No response or directives exist - proceed with our directive and parameters
            await self.respond(
                visitor,
                directives=[self.directive],
                parameters=params_to_pass,
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
