"""IntroInteractAction for welcoming first-time users.

This module provides IntroInteractAction, an InteractAction that detects
first-time users and adds an introductory directive to guide the persona response.
"""

import logging
from typing import TYPE_CHECKING

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class IntroInteractAction(InteractAction):
    """InteractAction that welcomes first-time users with an introductory message.

    IntroInteractAction:
    1. Checks if the user is a first-time user (no prior actions/events)
    2. Adds an introductory directive for PersonaAction to include in response
    3. Only executes once per conversation (first interaction only)

    Attributes:
        prompt: Introductory message template for first-time users
        weight: Execution weight (default: -50, runs after InteractRouter but before PersonaAction)
        anchors: Routing anchors (empty list - this runs conditionally based on user status)
    """

    directive: str = attribute(
        default=(
            "Introduce yourself by name and explain your role, then refer the first-time user to read your AI policy at https://platform.trueselph.com/policy. Do not say anything else."
        ),
        description="Introductory message for first-time users",
    )

    description: str = attribute(
        default="Introductory interact action for welcoming first-time users.",
        description="Action description"
    )

    weight: int = attribute(
        default=-75,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute intro action if user is first-time.

        Checks if this is a first-time user interaction and adds
        an introductory directive if so.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("IntroInteractAction: No interaction available")
            return

        try:
            # Check if this is a new user (first interaction)
            if not visitor.new_user:
                logger.debug("IntroInteractAction: Not a first-time user, skipping intro")
                return

            # Validate prompt is configured
            if not self.directive:
                logger.warning("IntroInteractAction: Directive not configured, skipping intro")
                return

            # Generate response via PersonaAction with directive
            if self.directive:
                await self.respond(
                    visitor,
                    directives=[self.directive],
                    parameters=self.parameters if self.parameters else None,
                    use_history=False,
                    with_utterance=False
                )

            logger.info("IntroInteractAction: Added introductory directive for first-time user")

        except Exception as e:
            logger.error(f"IntroInteractAction: Error during execution: {e}", exc_info=True)
            # Don't raise - allow other actions to continue

    async def healthcheck(self) -> bool | dict:
        """Perform health check on the action.

        Validates that the prompt is configured.

        Returns:
            True if healthy, dict with error details otherwise
        """
        if not self.directive:
            return {
                "status": False,
                "message": "Prompt is not set",
                "severity": "error",
            }

        return True
