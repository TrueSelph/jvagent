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
        default_factory=list, description="List of in-memory ResponseMessage IDs linked to this interaction (non-persisted references)"
    )
    streamed: bool = attribute(
        default=False, description="Whether this interaction used streaming"
    )
    # Processing tracking
    actions: List[str] = attribute(
        default_factory=list, description="Actions involved in processing (in order)"
    )
    directives: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Directives issued by non-persona actions. Each entry has structure: {'action_label': str, 'content': str, 'executed': bool}"
    )
    events: List[Dict[str, Any]] = attribute(
        default_factory=list, description="System events (logs). Each entry has structure: {'action_label': str, 'content': str}"
    )

    # Parameter tracking
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Applicable parameters for this interaction. Each entry should have 'action_label' and 'executed': bool keys"
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

    def add_directive(self, directive: str, action_label: str) -> None:
        """Add a directive to the interaction.

        Directives are instructions issued by non-persona actions.
        New directives are added with executed=False by default.

        Args:
            directive: Directive string to add
            action_label: Class name of the action that added this directive
        """
        if directive and action_label:
            entry = {
                "action_label": action_label,
                "content": directive,
                "executed": False
            }
            # Prevent duplicates based on content and action_label
            if entry not in self.directives:
                self.directives.append(entry)

    def add_event(self, event: str, action_label: str) -> None:
        """Add an event to the interaction.

        Events are logs and do not require execution tracking -
        their publication itself signifies execution.

        Args:
            event: Event string to add
            action_label: Class name of the action that added this event
        """
        if event and action_label:
            entry = {"action_label": action_label, "content": event}
            self.events.append(entry)  # Events can have duplicates (logs)

    def add_action(self, action_label: str) -> None:
        """Add an action to the processing record.

        Actions are recorded in order of execution.

        Args:
            action_label: Label of the action to add
        """
        if action_label and action_label not in self.actions:
            self.actions.append(action_label)

    def add_parameter(self, parameter: Dict[str, Any], action_label: str) -> None:
        """Add a parameter to the applicable parameters list.

        New parameters are added with executed=False by default.

        Args:
            parameter: Parameter data (id, condition, response, etc.)
            action_label: Class name of the action that added this parameter
        """
        if parameter:
            parameter["action_label"] = action_label
            # Ensure executed key is set to False if not already present
            if "executed" not in parameter:
                parameter["executed"] = False
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

    def get_unexecuted_directives(self) -> List[Dict[str, Any]]:
        """Get directives that have not yet been executed.

        Returns:
            List of unexecuted directive entries (dicts with action_label, content, executed=False)
        """
        return [d for d in self.directives if not d.get("executed", False)]

    def get_executed_directives(self) -> List[Dict[str, Any]]:
        """Get directives that have been executed.

        Returns:
            List of executed directive entries (dicts with action_label, content, executed=True)
        """
        return [d for d in self.directives if d.get("executed", False)]

    def get_unexecuted_parameters(self) -> List[Dict[str, Any]]:
        """Get parameters that have not yet been executed.

        Returns:
            List of unexecuted parameter entries (dicts with executed=False)
        """
        return [p for p in self.parameters if not p.get("executed", False)]

    def get_executed_parameters(self) -> List[Dict[str, Any]]:
        """Get parameters that have been executed.

        Returns:
            List of executed parameter entries (dicts with executed=True)
        """
        return [p for p in self.parameters if p.get("executed", False)]

    def get_directives_by_action(self, action_label: str) -> List[Dict[str, Any]]:
        """Get directives added by a specific action.

        Args:
            action_label: Class name of the action to filter by

        Returns:
            List of directive entries from the specified action
        """
        return [d for d in self.directives if d.get("action_label") == action_label]

    def get_parameters_by_action(self, action_label: str) -> List[Dict[str, Any]]:
        """Get parameters added by a specific action.

        Args:
            action_label: Class name of the action to filter by

        Returns:
            List of parameter entries from the specified action
        """
        return [p for p in self.parameters if p.get("action_label") == action_label]

    def get_events_by_action(self, action_label: str) -> List[Dict[str, Any]]:
        """Get events added by a specific action.

        Args:
            action_label: Class name of the action to filter by

        Returns:
            List of event entries from the specified action
        """
        return [e for e in self.events if e.get("action_label") == action_label]

    def set_to_executed(self, parameters: List[Dict[str, Any]] = [], directives: List[Dict[str, Any]] = []) -> None:
        """Mark directives and parameters as executed.

        Finds matching entries in self.directives and self.parameters by comparing
        action_label and content, then sets executed=True on matching entries in-place.

        Args:
            parameters: Parameter entries to mark as executed
            directives: Directive entries to mark as executed
        """
        # Mark matching directives as executed
        for directive_entry in directives:
            action_label = directive_entry.get("action_label")
            content = directive_entry.get("content")
            if action_label and content:
                for d in self.directives:
                    if d.get("action_label") == action_label and d.get("content") == content:
                        d["executed"] = True

        # Mark matching parameters as executed
        for parameter_entry in parameters:
            action_label = parameter_entry.get("action_label")
            # Match by action_label and a unique identifier if available (e.g., "id" or "condition")
            # If no unique identifier, match by action_label and all other keys
            for p in self.parameters:
                if p.get("action_label") == action_label:
                    # Try to match by id if available
                    if "id" in parameter_entry and "id" in p:
                        if p.get("id") == parameter_entry.get("id"):
                            p["executed"] = True
                    # Otherwise, match by all keys except executed
                    else:
                        # Create copies without executed key for comparison
                        p_copy = {k: v for k, v in p.items() if k != "executed"}
                        param_copy = {k: v for k, v in parameter_entry.items() if k != "executed"}
                        if p_copy == param_copy:
                            p["executed"] = True

    def add_message(self, message_id: str) -> None:
        """Link an in-memory ResponseMessage ID to this interaction.

        Note: ResponseMessage objects are non-persisted, so these IDs are
        only for tracking/logging purposes and cannot be queried from the database.

        Args:
            message_id: In-memory ResponseMessage ID to link
        """
        if message_id and message_id not in self.messages:
            self.messages.append(message_id)

    def get_directives(self) -> List[Dict[str, Any]]:
        """Get all directives.

        Returns:
            List of directive entries (dicts with action_label, content, executed)
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

    def get_state(self) -> Dict[str, Any]:
        """Get comprehensive interaction state for observability.

        Returns:
            Dictionary containing full interaction state including:
            - All directives and parameters with execution status
            - All events (logs)
            - Actions executed
            - Timestamps
            - Full interaction metadata
        """
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "utterance": self.utterance,
            "channel": self.channel,
            "response": self.response,
            "actions": self.actions,
            "directives": self.directives,  # Includes executed status
            "parameters": self.parameters,  # Includes executed status
            "events": self.events,  # Logs - no execution status
            "model_log": self.model_log,
            "interpretation": self.interpretation,
            "anchors": self.anchors,
            "routing_confidence": self.routing_confidence,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "closed": self.closed,
            "streamed": self.streamed,
        }

    def to_transcript_entry(self) -> Dict[str, Any]:
        """Convert interaction to transcript entry format.

        Returns:
            Dictionary with human and ai messages
        """
        entry: Dict[str, Any] = {"human": self.utterance}
        if self.response:
            entry["ai"] = self.response
        if self.events:
            # Extract content from event entries
            event_contents = [e.get("content", str(e)) for e in self.events]
            entry["events"] = event_contents
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
