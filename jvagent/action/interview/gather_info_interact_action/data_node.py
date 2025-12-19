"""DataNode for interview question handling.

This module provides DataNode, a node that represents individual interview questions
in the gather info interview process.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from .gather_info_walker import GatherInfoWalker
logger = logging.getLogger(__name__)


class DataNode(Node):
    """InteractAction that retrieves context from vector stores and adds directives.

    RetrievalInteractAction:
    1. Uses the interaction's interpretation (or utterance as fallback) as search query
    2. Retrieves relevant context from a configured vector store
    3. Formats retrieved results into a structured directive
    4. Adds the directive to the interaction for PersonaAction to use

    Attributes:
        collection: Collection name (inherited but unused)
        weight: Execution weight (default: -40)
        directive_template: Optional template for formatting directives
        state: Dictionary containing 'name' and 'question' for this interview item
    """
    description: str = "Interview question node for gathering user information"
    collection: str = attribute(
        default="",
        description="Collection name to search in the vector store",
    )
    weight: int = attribute(
        default=-40,
        description="Execution weight (runs after InteractRouter but before PersonaAction)",
    )
    directive_template: Optional[str] = attribute(
        default="""
    Tailor your response to get the information needed based on the following description:
    {description}
    Avoid asking for other information not related to this description unless specified elsewhere.  {question}

    {instructions}
    """,
        description="Optional template for formatting the directive. Uses default structured format if not provided. Placeholder: {results}",
    )
    instructions_template: Optional[str] = attribute(
        default="Take note of the following additional instructions while responding to the user but avoid mentioning them unless it is needed:\n {instructions}",
        description="Optional instructions for the directive. Uses default structured format if not provided. Placeholder: {results}",
    )
    state: Dict[str, Any] = attribute(
        default={},
        description="State index passed from parent action",
    )
    label: str = attribute(
        default_factory=str,
        description="Label for the node",
    )

    async def on_register(self) -> None:
        """Register the action with the action registry."""
        logger.debug(f"DataNode registered with state: {self.state}")

    async def execute(self, session: "GatherInfoWalker") -> Optional[str]:
        """Execute data node to check if info is needed and return directive.

        Args:
            session: The GatherInfoWalker visiting this node

        Returns:
            Directive string if information is needed, None otherwise
        """
        logger.debug(f"DataNode executed for {self.label}")

        if not self.state.get("name", ""):
            logger.debug("No name in state")
            return None

        # Safely check if this field has been answered
        interview_session = session.conversation.data_get("interview_session")
        if interview_session and isinstance(interview_session, dict):
            if self.label in interview_session:
                logger.debug(f"DataNode: {self.label} already answered")
                return None

        constraints = self.state.get("constraints", {})
        question = self.state.get("question", "")

        description = constraints.get("description", "")
        instructions = constraints.get("instructions", "")


        if instructions:
            instructions = self.instructions_template.format(instructions=instructions)

        directive = self.directive_template.format(
            description=description,
            instructions=instructions,
            question=question,
        )
        if directive:
            return directive
        else:
            logger.debug("DataNode got no directive, something went wrong")




# @unique
class SessionState():
    OPEN = 'OPEN', # session is open for extractions -> collect responses
    COMPLETED = 'COMPLETED', # all responses have been collected -> prompt to confirm
    CONFIRMED = 'CONFIRMED', # user has confirmed all responses -> close the session
    REVISION = 'REVISION', # user has not confirmed responses -> prompt for review -> set to complete/ready
    ABORTED = 'ABORTED', # user has chosen to abandon the process -> remove the session


class InterviewSession():
    state: SessionState = SessionState.OPEN
    all_fields: list = []
    required_fields: list = []
    active_field: str = ""
    responses: dict = {}

    def get_state(self) -> SessionState:
        return self.state

    def set_state(self, state: SessionState):
        self.state = state

    def get_next_field(self) -> Optional[str]:
        response_fields = self.responses.keys()
        for item in self.all_fields:
            if item not in response_fields:
                self.active_field = item
                return item
        return None

    def on_required_field(self) -> bool:
        return (self.get_next_field() in self.get_required_fields())

    def get_answered_fields(self) -> list:
        return list(self.responses.keys()) or []

    def get_unanswered_fields(self) -> list:
        return [field for field in self.all_fields if field not in self.get_answered_fields()]

    def get_required_fields(self) -> list:
        return self.required_fields

    def get_response(self, field: str) -> str:
        return self.responses.get(field, "")

    def set_response(self, field: str, response: str):
        self.responses[field] = response

    def del_response(self, field: str):
        if field in self.responses:
            del self.responses[field]
