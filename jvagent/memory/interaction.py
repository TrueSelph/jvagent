"""Interaction node for representing single exchanges within a conversation."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index


@compound_index([("context.conversation_id", 1), ("context.started_at", -1)], name="conv_timestamp")
class Interaction(Node):
    """Single exchange within a Conversation.

    The Interaction node represents a single user-agent exchange. It is a Node
    (not Object) to enable edge relationships and cascade deletes with the
    parent Conversation.

    Entity Relationships:
        - Connected to Conversation via incoming edge (first interaction only)
        - Chained to other Interactions via bidirectional edges:
          Interaction1 <-> Interaction2 <-> Interaction3
          (allows forward and backward traversal)

    Cascade Delete Behavior:
        - Deleting the parent Conversation cascades to delete all chained Interactions

    Attributes:
        conversation_id: Parent conversation ID
        user_id: User ID
        utterance: User input text
        channel: Communication channel
        response: Agent response text
        canned_response: Immediate response before full processing
        actions: Actions involved in processing (in order of execution)
        directives: Directives issued by non-persona actions
        events: System events
        parameters: Applicable parameters for this interaction
        model_log: Collection of ModelActionResult data for all model calls
        started_at: Interaction start timestamp
        completed_at: Interaction completion timestamp
        closed: Whether the interaction is closed
    """

    # Context
    conversation_id: str = attribute(
        indexed=True, default="", description="Parent conversation ID"
    )
    user_id: str = attribute(default="", description="User ID")
    utterance: str = attribute(default="", description="User input text")
    channel: str = attribute(default="default", description="Communication channel")

    # Response
    response: Optional[str] = attribute(
        default=None, description="Agent response text"
    )
    messages: List[str] = attribute(
        default_factory=list, description="List of ResponseMessage IDs linked to this interaction"
    )
    streamed: bool = attribute(
        default=False, description="Whether this interaction used streaming"
    )
    # Processing tracking
    actions: List[str] = attribute(
        default_factory=list, description="Actions involved in processing (in order)"
    )
    directives: List[str] = attribute(
        default_factory=list, description="Directives issued by non-persona actions"
    )
    events: List[str] = attribute(
        default_factory=list, description="System events"
    )

    # Parameter tracking
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Applicable parameters for this interaction"
    )

    # Model call log
    model_log: List[Dict[str, Any]] = attribute(
        default_factory=list, description="ModelActionResult data for all model calls"
    )

    # Routing (from InteractRouter)
    interpretation: Optional[str] = attribute(
        default=None,
        description="LLM-generated interpretation of user intent (< 50 words)"
    )
    anchors: List[str] = attribute(
        default_factory=list,
        description="Matched entity names from anchor matching"
    )
    routing_confidence: Optional[float] = attribute(
        default=None,
        description="Confidence score for routing match (0.0-1.0)"
    )

    # Timestamps
    started_at: datetime = attribute(
        indexed=True, index_direction=-1,
        default_factory=lambda: datetime.now(timezone.utc),
        description="Interaction start timestamp"
    )
    completed_at: Optional[datetime] = attribute(
        default=None, description="Interaction completion timestamp"
    )
    closed: bool = attribute(
        default=False, description="Whether the interaction is closed"
    )

    def add_directive(self, directive: str) -> None:
        """Add a directive to the interaction.

        Directives are instructions issued by non-persona actions.

        Args:
            directive: Directive string to add
        """
        if directive and directive not in self.directives:
            self.directives.append(directive)

    def add_event(self, event: str) -> None:
        """Add an event to the interaction.

        Args:
            event: Event string to add
        """
        if event:
            self.events.append(event)

    def add_action(self, action_label: str) -> None:
        """Add an action to the processing record.

        Actions are recorded in order of execution.

        Args:
            action_label: Label of the action to add
        """
        if action_label and action_label not in self.actions:
            self.actions.append(action_label)

    def add_parameter(self, parameter: Dict[str, Any]) -> None:
        """Add a parameter to the applicable parameters list.

        Args:
            parameter: Parameter data (id, condition, response, etc.)
        """
        if parameter:
            self.parameters.append(parameter)

    def add_model_result(self, model_result: Dict[str, Any]) -> None:
        """Add a model call result to the log.

        Args:
            model_result: ModelActionResult.to_dict() data
        """
        if model_result:
            self.model_log.append(model_result)

    def has_response(self) -> bool:
        """Check if the interaction has a response.

        Returns:
            True if response is set, False otherwise
        """
        return self.response is not None

    def set_response(self, content: str) -> None:
        """Set the interaction response.

        Args:
            content: Response content
        """
        self.response = content

    def add_message(self, message_id: str) -> None:
        """Link a ResponseMessage to this interaction.

        Args:
            message_id: ResponseMessage ID to link
        """
        if message_id and message_id not in self.messages:
            self.messages.append(message_id)

    def get_directives(self) -> List[str]:
        """Get all directives.

        Returns:
            List of directive strings
        """
        return self.directives

    def close_interaction(self) -> None:
        """Close the interaction."""
        self.closed = True
        self.completed_at = datetime.now(timezone.utc)

    def get_duration(self) -> float:
        """Get interaction duration in seconds.

        Returns:
            Duration in seconds, or 0 if not completed
        """
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    def is_new_user(self) -> bool:
        """Check if this is a new user interaction.

        Returns:
            True if this is the first interaction (no prior events/actions)
        """
        return len(self.actions) == 0 and len(self.events) == 0

    def to_transcript_entry(self) -> Dict[str, Any]:
        """Convert interaction to transcript entry format.

        Returns:
            Dictionary with human and ai messages
        """
        entry: Dict[str, Any] = {"human": self.utterance}
        if self.response:
            entry["ai"] = self.response
        if self.events:
            entry["events"] = self.events
        return entry

    async def get_agent(self) -> Optional[Any]:
        """Get the Agent node this Interaction belongs to.

        Traverses: Interaction -> Conversation (via conversation_id) -> User -> Memory -> Agent.

        Returns:
            Agent instance if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation

        # Get Conversation node using conversation_id
        if self.conversation_id:
            conversation = await Conversation.get(self.conversation_id)
            if conversation:
                # Get Agent from Conversation using its get_agent() method
                return await conversation.get_agent()
        return None

    async def get_next_interaction(self) -> Optional["Interaction"]:
        """Get the next interaction in the chain (forward traversal).

        Returns:
            Next Interaction node, or None if this is the last interaction
        """
        from jvagent.memory.interaction import Interaction

        # Get the next interaction via outgoing edges (forward direction)
        # Filter by conversation_id to ensure it's part of the same chain
        # With bidirectional edges, there should be at most one next interaction
        next_int = await self.node(
            node=Interaction, direction="out", conversation_id=self.conversation_id
        )
        
        # Verify timestamp ordering (safety check)
        if next_int and next_int.started_at >= self.started_at:
            return next_int
        return None

    async def get_previous_interaction(self) -> Optional["Interaction"]:
        """Get the previous interaction in the chain (backward traversal).

        Returns:
            Previous Interaction node, or None if this is the first interaction
        """
        from jvagent.memory.interaction import Interaction

        # Get the previous interaction via incoming edges (backward direction)
        # Filter by conversation_id to ensure it's part of the same chain
        # With bidirectional edges, there should be at most one previous interaction
        prev_int = await self.node(
            node=Interaction, direction="in", conversation_id=self.conversation_id
        )
        
        # Verify timestamp ordering (safety check)
        if prev_int and prev_int.started_at <= self.started_at:
            return prev_int
        return None
