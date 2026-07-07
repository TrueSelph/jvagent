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
    2. Adds an introductory response-shaping parameter for the responder
       (ReplyAction) to weave into the reply
    3. Only executes once per conversation (first interaction only)

    Attributes:
        prompt: Introductory message template for first-time users
        weight: Execution weight (default: -300, runs before the Orchestrator at -200)
        anchors: Routing anchors (empty list - this runs conditionally based on user status)
    """

    directive: str = attribute(
        default=(
            "This is the visitor's first message: open your reply by briefly "
            "introducing yourself by name and what you help with (one short "
            "sentence), then continue naturally into the rest of your reply. Do "
            "not mention any knowledge cutoff, training date, underlying model, "
            "or provider."
        ),
        description=(
            "First-message self-introduction. Applied as a response-shaping "
            "parameter (HOW) so the greeting is woven into the same reply as any "
            "substantive answer, rather than emitted as a separate directive."
        ),
    )

    description: str = attribute(
        default="Introductory interact action for welcoming first-time users.",
        description="Action description",
    )

    weight: int = attribute(
        default=-300,
        description="Execution weight (runs before the Orchestrator at -200)",
    )

    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing (first-time user intro handler).",
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Add a first-message self-introduction as a response-shaping parameter.

        Runs at weight -300 (before the executive/router), so its parameter is on
        the interaction before any downstream action queues an answer directive.
        Contributing a *parameter* (HOW) rather than a *directive* (WHAT) lets the
        single ReplyAction compose weave the greeting into the same reply as the
        substantive answer, instead of emitting the intro as a separate mandated
        section that reads as a second, disjoint blob.

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

            # Validate the intro text is configured
            if not self.directive:
                logger.warning(
                    "IntroInteractAction: Directive not configured, skipping intro"
                )
                await visitor.unrecord_action_execution()
                return

            # Contribute the intro as a response-shaping parameter (not a
            # directive) so it modulates HOW the reply opens rather than adding a
            # separate mandated content section. Interaction-scoped, so it only
            # affects this first reply. add_parameter sets action_name + saves.
            await visitor.add_parameter({"response": self.directive})

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
