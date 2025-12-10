"""IntroInteractAction for welcoming first-time users.

This module provides IntroInteractAction, an InteractAction that detects
first-time users and adds an introductory directive to guide the persona response.
"""

import logging
from typing import TYPE_CHECKING, Optional, Any

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
            "In a natural and brief manner:\n"
            "a. Introduce yourself by name and explain your role\n"
            "b. Refer the first-time user to read your AI policy at https://platform.trueselph.com/policy before continuing. "
            "It contains our privacy policy."
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

    anchors: list = attribute(
        default_factory=list,
        description="Routing anchors (empty - conditional execution based on user status)",
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
            if not self._is_new_user(interaction):
                logger.debug("IntroInteractAction: Not a first-time user, skipping intro")
                return

            # Validate prompt is configured
            if not self.directive:
                logger.warning("IntroInteractAction: Directive not configured, skipping intro")
                return

            # Add introductory directive to interaction
            interaction.add_directive(self.directive)
            if self.directive:
                directive = self.directive
                interaction.add_directive(directive)
                await interaction.save()
                directive_added = True
                logger.debug(
                    f"ExampleInteractAction: Added directive with {directive} retrieved context items"
                )
            else:
                logger.debug("ExampleInteractAction: No results found, no directive added")

            # If a directive was produced, optionally invoke PersonaAction to produce a response
            if directive_added:
                try:
                    persona = await self._get_persona_action()
                    if persona:
                        # PersonaAction.respond now supports visitor (for streaming via ResponseBus)
                        visitor.stream_mode = True
                        response = await persona.respond(interaction, visitor=visitor)
                        if response and visitor.interaction:
                            visitor.interaction.set_response(response)
                            await visitor.interaction.save()
                    else:
                        logger.debug("RetrievalInteractAction: PersonaAction not found; skipping auto-respond")
                except Exception as e:
                    logger.error(f"RetrievalInteractAction: Error calling PersonaAction.respond: {e}", exc_info=True)

            logger.info("IntroInteractAction: Added introductory directive for first-time user")

        except Exception as e:
            logger.error(f"IntroInteractAction: Error during execution: {e}", exc_info=True)
            # Don't raise - allow other actions to continue

    def _is_new_user(self, interaction: "Interaction") -> bool:
        """Check if this is a first-time user interaction.

        Uses the Interaction's is_new_user() method which checks if
        there are no prior actions or events.

        Args:
            interaction: The interaction to check

        Returns:
            True if first-time user, False otherwise
        """
        return interaction.is_new_user()

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
