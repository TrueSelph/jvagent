"""Interview Action Implementation

This is a boilerplate action that demonstrates the structure and lifecycle
of a custom action in jvagent.

All configuration is done via typed Pydantic fields, not a config dictionary.
"""

import logging
import json
from typing import Any, Dict, List, Optional
from jvagent.action.interact.base import InteractAction
from jvagent.memory import Interaction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute

from .gather_info_interact_action.gather_info_interact_action import GatherInfoInteractAction

logger = logging.getLogger(__name__)


class InterviewInteractAction(InteractAction):
    """InteractAction that retrieves context from vector stores and adds directives.

    InterviewInteractAction:
    1. Routes to the appropriate action based on the state of the interaction

    Attributes:
        question_index: List of questions to which this action is enabled
    """
    description: str = "InterviewInteractAction that routes to the appropriate action based on the state of the interaction."
    collection: str = attribute(
        default="",
        description="Collection name to search in the vector store",
    )
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive_template: Optional[str] = attribute(
        default=None,
        description="Optional template for formatting the directive. Uses default structured format if not provided. Placeholder: {results}",
    )
    state_index: List[Dict[str, Any]] = attribute(
        default=[
            {
                "name": "user_name",
                "constraints": {
                    "description": "The user's fullname",
                    "instructions": "The user's fullname must include their first and last name.",
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "available_times",
                "question": "What times are you available to train?",
                "constraints": {
                    "description": "The user's available times",
                    "type": "string"
                },
                "required": True
            },
            {
                "name": "user_email",
                "question": "What is your email?",
                "constraints": {
                    "description": "The user's email",
                    "type": "string"
                },
                "required": True
            },
        ],
        description="List of states to which this action is enabled",
    )

    async def on_register(self) -> None:
        """Register the action with the action registry."""
        logger.info("InterviewInteractAction on_register")
        # must pass agent_id to any action when using create
        gather_info_interact_action = await GatherInfoInteractAction.create(agent_id=self.agent_id, state_index=self.state_index)
        await self.connect(gather_info_interact_action, direction="both")
        await gather_info_interact_action.on_register()
        print("\033[92mInterviewInteractAction: Registered gather info interact action\n",interview_interact_action.always_execute)

    async def on_reload(self) -> None:
        """Register the action with the action registry."""
        logger.info("InterviewInteractAction on_reload")
        # must pass agent_id to any action when using create
        gather_info_interact_action = await GatherInfoInteractAction.create(agent_id=self.agent_id, **{"state_index": self.state_index})
        await self.connect(gather_info_interact_action, direction="both")
        await gather_info_interact_action.on_reload()

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute example interact action and add directive to interaction.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("InterviewInteractAction: No interaction available")
            return

        result = "Tell the user that they are currently interacting with an AI agent on JVAgent and can learn more about jvagent at https://jvagent.com/docs"
        if result:
            # Generate response via PersonaAction with directive
            # await self.respond(
            #     visitor,
            #     directives=[result],
            #     parameters=self.parameters if self.parameters else None
            # )
            logger.debug(
                f"InterviewInteractAction: Generated response with directives and parameters"
            )
        else:
            logger.debug("InterviewInteractAction: No result, skipping response")
