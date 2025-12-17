"""RetrievalInteractAction for retrieving context from vector stores.

This module provides RetrievalInteractAction, an InteractAction that retrieves
relevant context from a vector store using the interaction's interpretation (or
utterance as fallback) and composes a structured directive for PersonaAction.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import sys
import os

# Add sibling directory to path to allow importing other actions
# current_dir = os.path.dirname(os.path.abspath(__file__))
# parent_dir = os.path.dirname(current_dir)
# if parent_dir not in sys.path:
#     sys.path.append(parent_dir)

from ..example_interact_action.example_interact_action import ExampleInteractAction

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class MultiturnInteractAction(InteractAction):
    """InteractAction that retrieves context from vector stores and adds directives.

    RetrievalInteractAction:
    1. Uses the interaction's interpretation (or utterance as fallback) as search query
    2. Retrieves relevant context from a configured vector store
    3. Formats retrieved results into a structured directive
    4. Adds the directive to the interaction for PersonaAction to use

    Attributes:
        vectorstore_action_type: Entity type of VectorStore action (e.g., "TypesenseVectorStore")
        collection: Collection name to search in (default: "default")
        k: Number of search results to retrieve (default: 10)
        weight: Execution weight (default: -50, runs after InteractRouter but before PersonaAction)
        directive_template: Optional template for formatting the directive with placeholder: {results}
        min_score_threshold: Optional minimum similarity score to include results
    """
    description: str = "MultiturnInteractAction that maintains context for multi-turn interactions such as signing up for training."
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
                "item": "user_name",
                "question": "What is your name?",
                "constraints": {
                    "description": "The user's fullname",
                    "instructions": "The user's fullname must include their first and last name.",
                    "type": "string",
                },
                "required": True
            },
            {
                "item": "available_times",
                "question": "What times are you available to train?",
                "constraints": {
                    "description": "The user's available times",
                    "type": "string"
                },
                "required": True
            }
        ],
        description="List of states to which this action is enabled",
    )

    async def on_register(self) -> None:
        """Register the action with the action registry."""
        logger.info("MultiturnInteractAction registered")
        # must pass agent_id to any action when using create
        example_interact_action = await ExampleInteractAction.create(agent_id=self.agent_id, **{"state_index": self.state_index})
        await self.connect(example_interact_action, direction="both")
        print("\033[92mMultiturnInteractAction: Registered example interact action\n",example_interact_action.always_execute)

    async def on_reload(self) -> None:
        """Register the action with the action registry."""
        logger.info("MultiturnInteractAction reloaded")
        example_interact_action = await ExampleInteractAction.create(agent_id=self.agent_id, **{"state_index": self.state_index})

        await self.connect(example_interact_action, direction="both")
        print("\033[92mMultiturnInteractAction: Registered example interact action\033[0m\n",example_interact_action.always_execute)

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute example interact action and add directive to interaction.

        Args:
            visitor: The InteractWalker visiting this action
        """
        interaction = visitor.interaction
        if not interaction:
            logger.warning("ExampleInteractAction: No interaction available")
            return

        result = "Tell the user that they are currently interacting with an AI agent on JVAgent and can learn more about jvagent at https://jvagent.com/docs"
        print("\033[92mMultiturnInteractAction: Generated result with directives and parameters\033[0m")
        if result:
            # Generate response via PersonaAction with directive
            # await self.respond(
            #     visitor,
            #     directives=[result],
            #     parameters=self.parameters if self.parameters else None
            # )
            logger.debug(
                f"MultiturnInteractAction: Generated response with directives and parameters"
            )
        else:
            logger.debug("ExampleInteractAction: No result, skipping response")

    def _format_directive(self, results: List[Dict[str, Any]]) -> str:
        """Format retrieved results into a structured directive.

        Args:
            results: List of search results, each containing document, score, distance, metadata

        Returns:
            Formatted directive string
        """
        # Format results as a string
        results_parts = []
        for i, result in enumerate(results, 1):
            # Extract document content
            document = result.get("document", {})
            if isinstance(document, dict):
                content = document.get("content", str(document))
            else:
                content = str(document)

            # Get score if available
            score = result.get("score")
            score_str = f" (Relevance score: {score:.3f})" if score is not None else ""

            # Format each result
            results_parts.append(f"{i}. {content}{score_str}")

        results_str = "\n".join(results_parts)

        if self.directive_template:
            # Use custom template if provided
            # Template should use {results} placeholder
            return self.directive_template.format(results=results_str)

        # Default structured format
        directive_parts = ["Context retrieved from knowledge base:\n"]
        directive_parts.append(results_str)
        directive_parts.append("\nUse this context to inform your response to the user's query.")

        return "\n".join(directive_parts)


# @unique
class SessionState():
    OPEN = 'OPEN', # session is open for extractions -> collect responses
    COMPLETED = 'COMPLETED', # all responses have been collected -> prompt to confirm
    CONFIRMED = 'CONFIRMED', # user has confirmed all responses -> close the session
    REVISION = 'REVISION', # user has not confirmed responses -> prompt for review -> set to complete/ready
    ABORTED = 'ABORTED', # user has chosen to abandon the process -> remove the session


class InterviewSession():
    state:SessionState = SessionState.OPEN
    all_fields: list = []
    required_fields: list = []
    active_field: str = ""
    responses: dict = {}

    def get_state() -> SessionState:
        return self.state

    def set_state(state:SessionState):
        self.state = state

    def get_next_field() -> str:

        response_fields = self.responses.keys()

        for item in self.all_fields:
            if item not in response_fields:
                self.active_field = item
                return item

        return None

    def on_required_field() -> bool:
        return (self.get_next_field() in self.get_required_fields())

    def get_answered_fields() -> list:
        return list(self.responses.keys()) or []

    def get_unanswered_fields() -> list:
        return [field for field in self.all_fields if field not in self.get_answered_fields()]

    def get_required_fields() -> list:
        return self.required_fields

    def get_response(field: str) -> str:
        return self.responses.get(field, "")

    def set_response(field: str, response: str):
        self.responses[field] = response

    def del_response(field: str):
        del self.responses[field]
