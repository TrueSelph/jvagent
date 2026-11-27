"""IntroInteractAction for welcoming first-time users.

This module provides IntroInteractAction, an InteractAction that detects
first-time users and adds an introductory parameter for both the orchestrator
(so it authors the first reply with the custom intro) and ReplyAction (so
compose can still weave the greeting if a bare directive slips through).
"""

import logging
from typing import TYPE_CHECKING, Union

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.parameters import SCOPE_ORCHESTRATION, SCOPE_RESPONSE

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_INTRO_PARAM_KEY = "intro.first_message"


class IntroInteractAction(InteractAction):
    """InteractAction that welcomes first-time users with an introductory message.

    IntroInteractAction:
    1. Checks if the user is a first-time visitor to this agent (``visitor.new_user``)
    2. Publishes the intro as orchestration-scoped and response-scoped parameters
       so the executive sees the custom intro when authoring the reply, and
       ReplyAction can still weave it at compose time
    3. Only runs on first engagement with this agent (memory-scoped ``new_user``;
       skipped when resuming a session or when the user already exists under Memory)

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
            "First-message self-introduction. Applied as orchestration- and "
            "response-scoped parameters (HOW) so the executive authors the intro "
            "into the reply and compose can still weave it — rather than emitting "
            "a separate directive."
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
        """Publish the first-message intro as orchestration + response parameters.

        Runs at weight -300 (before the executive/router), so both parameters are
        on the interaction before any downstream action queues an answer
        directive. Orchestration scope reaches the executive system prompt;
        response scope reaches ReplyAction compose. Contributing a *parameter*
        (HOW) rather than a *directive* (WHAT) lets one reply weave the greeting
        into the substantive answer instead of a separate mandated section.

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

            # Dual-scope: executive authors the intro into the reply directive;
            # compose can still weave it if a bare greeting slips through.
            # Interaction-scoped only for this first reply. add_parameter sets
            # action_name + saves.
            await visitor.add_parameter(
                {
                    "key": _INTRO_PARAM_KEY,
                    "scope": SCOPE_ORCHESTRATION,
                    "response": self.directive,
                }
            )
            await visitor.add_parameter(
                {
                    "key": _INTRO_PARAM_KEY,
                    "scope": SCOPE_RESPONSE,
                    "response": self.directive,
                }
            )

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
