"""IntroInteractAction for welcoming first-time users.

This module provides IntroInteractAction, an InteractAction that detects
first-time users and adds an introductory directive to guide the persona response.
"""

import logging
from typing import TYPE_CHECKING, Union

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
        weight: Execution weight (default: -300, runs before InteractRouter and CockpitInteractAction at -200)
        anchors: Routing anchors (empty list - this runs conditionally based on user status)
    """

    directive: str = attribute(
        default=("Introduce yourself and briefly explain your capabilities."),
        description="Introductory message for first-time users",
    )

    description: str = attribute(
        default="Introductory interact action for welcoming first-time users.",
        description="Action description",
    )

    weight: int = attribute(
        default=-300,
        description="Execution weight (runs before InteractRouter and CockpitInteractAction at -200)",
    )

    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing (first-time user intro handler).",
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
            await visitor.unrecord_action_execution()
            return

        try:
            # Check if this is a new user (first interaction)
            if not visitor.new_user:
                logger.debug(
                    "IntroInteractAction: Not a first-time user, skipping intro"
                )
                await visitor.unrecord_action_execution()
                return

            # Validate prompt is configured
            if not self.directive:
                logger.warning(
                    "IntroInteractAction: Directive not configured, skipping intro"
                )
                await visitor.unrecord_action_execution()
                return

            # Add the directive via visitor so action_name is set and interaction is saved
            await visitor.add_directive(self.directive)

        except Exception as e:
            logger.error(
                f"IntroInteractAction: Error during execution: {e}", exc_info=True
            )
            await visitor.unrecord_action_execution()
            # Don't raise - allow other actions to continue

    async def healthcheck(self) -> Union[bool, dict]:
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
