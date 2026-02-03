"""Interaction node for representing single exchanges within a conversation."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from jvagent.action.model.base import logger
from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index
from jvspatial.core.mixins import DeferredSaveMixin

if TYPE_CHECKING:
    from jvagent.memory.user import User
    from jvagent.memory.conversation import Conversation


@compound_index([("context.conversation_id", 1), ("context.started_at", -1)], name="conv_timestamp")
class Interaction(DeferredSaveMixin, Node):
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
        session_id: Session identifier for this interaction
        response: Agent response text
        canned_response: Immediate response before full processing
        actions: Actions involved in processing (in order of execution)
        directives: Directives issued by non-persona actions
        events: System events
        parameters: Applicable parameters for this interaction
        observability_metrics: Aggregated observability events (model calls, embeddings, etc.)
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
    session_id: str = attribute(default="", description="Session identifier for this interaction")

    canned_response: Optional[str] = attribute(
        default=None,
        description="Immediate filler response before full processing (e.g., 'Let me see..', 'One moment..')"
    )
    
    # Response
    response: Optional[str] = attribute(
        default=None, description="Agent response text (accumulated from stream chunks and ad hoc messages)"
    )

    # Routing (from InteractRouter)
    interpretation: Optional[str] = attribute(
        default=None,
        description="LLM-generated interpretation of user intent (< 80 words)"
    )
    intent_type: Optional[str] = attribute(
        default=None,
        description="Classified intent type: REQUEST, QUERY, ANSWER, NAVIGATION, CONTINUATION, or AMBIGUOUS"
    )
    anchors: List[str] = attribute(
        default_factory=list,
        description="Matched entity names from anchor matching"
    )

    # Processing tracking
    actions: List[str] = attribute(
        default_factory=list, description="Actions involved in processing (in order)"
    )
    directives: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Directives issued by non-persona actions. Each entry has structure: {'action_name': str, 'content': str, 'executed': bool}"
    )
    events: List[Dict[str, Any]] = attribute(
        default_factory=list, description="System events (logs). Each entry has structure: {'action_name': str, 'content': str}"
    )

    # Parameter tracking
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Applicable parameters for this interaction. Each entry should have 'action_name' and 'executed': bool keys"
    )

    # Streaming and observability
    streamed: bool = attribute(
        default=False, description="Whether this interaction used streaming"
    )
    observability_metrics: List[Dict[str, Any]] = attribute(
        default_factory=list, description="Aggregated observability events (model calls, embeddings, etc.)"
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

    def add_directive(self, directive: str, action_name: str) -> bool:
        """Add a directive to the interaction.

        Directives are instructions issued by non-persona actions.
        New directives are added with executed=False by default.
        Prevents duplicates: no identical directives from the same action are added twice.

        Args:
            directive: Directive string to add
            action_name: Class name (camelCase) of the action that added this directive

        Returns:
            True if directive was added (not a duplicate), False if duplicate was found
        """
        if directive and action_name:
            # Check for duplicates: same action_name and same content
            for existing in self.directives:
                if existing.get("action_name") == action_name and existing.get("content") == directive:
                    return False  # Duplicate found, skip adding
            
            entry = {
                "action_name": action_name,
                "content": directive,
                "executed": False
            }
            self.directives.append(entry)
            return True  # Added
        return False  # Invalid input

    def add_event(self, event: str, action_name: str) -> bool:
        """Add an event to the interaction.

        Events are logs and do not require execution tracking -
        their publication itself signifies execution.

        Args:
            event: Event string to add
            action_name: Class name (camelCase) of the action that added this event

        Returns:
            True if event was added, False if invalid input
        """
        if event and action_name:
            entry = {"action_name": action_name, "content": event}
            self.events.append(entry)  # Events can have duplicates (logs)
            return True  # Added
        return False  # Invalid input

    def record_action_execution(self, action_name: str) -> None:
        """Record an action execution in the processing log.

        Actions are recorded in order of execution. The same action can be
        recorded multiple times if it executes multiple times, preserving
        the execution sequence.

        Args:
            action_name: Class name (camelCase) of the action to record
        """
        if action_name:
            self.actions.append(action_name)

    def unrecord_action_execution(self, action_name: str) -> None:
        """Remove an action execution from the processing log.

        Removes the last occurrence of the action name to preserve execution
        order for other actions. This is used when an action needs to opt out
        of being recorded (e.g., if it determines it shouldn't have executed).

        Args:
            action_name: Class name (camelCase) of the action to unrecord
        """
        if action_name and action_name in self.actions:
            # Remove the last occurrence to preserve ordering of other actions
            # Reverse iterate to find and remove the last occurrence
            for i in range(len(self.actions) - 1, -1, -1):
                if self.actions[i] == action_name:
                    self.actions.pop(i)
                    logger.warning(f"Interaction.unrecord_action_execution: Unrecorded action {action_name}")
                    break

    def add_parameter(self, parameter: Dict[str, Any], action_name: str) -> bool:
        """Add a parameter to the applicable parameters list.

        New parameters are added with executed=False by default.
        Prevents duplicates: no identical parameters from the same action are added twice.
        Parameters are considered identical if they have the same action_name and
        the same values for all keys (excluding 'executed' and 'action_name' which are set automatically).

        Args:
            parameter: Parameter data (id, condition, response, etc.)
            action_name: Class name (camelCase) of the action that added this parameter

        Returns:
            True if parameter was added (not a duplicate), False if duplicate was found
        """
        if not parameter:
            return False
        
        # Check for duplicates: same action_name and same parameter content
        # Compare all keys except 'executed' and 'action_name' (which are set automatically)
        param_copy = {k: v for k, v in parameter.items() if k not in ("executed", "action_name")}
        
        for existing in self.parameters:
            if existing.get("action_name") == action_name:
                # Compare parameter content (excluding executed and action_name)
                existing_copy = {k: v for k, v in existing.items() if k not in ("executed", "action_name")}
                if existing_copy == param_copy:
                    return False  # Duplicate found, skip adding
        
        # Not a duplicate, add it
        parameter["action_name"] = action_name
        # Ensure executed key is set to False if not already present
        if "executed" not in parameter:
            parameter["executed"] = False
        self.parameters.append(parameter)
        return True  # Added

    def add_parameters(self, parameters: List[Dict[str, Any]], action_name: str) -> bool:
        """Add multiple parameters to the interaction.

        Bulk convenience method that adds multiple parameters with the same action_name.
        Prevents duplicates: no identical parameters from the same action are added twice.

        Args:
            parameters: List of parameter dictionaries to add
            action_name: Class name (camelCase) of the action that added these parameters

        Returns:
            True if any parameter was added (not a duplicate), False if all were duplicates or empty
        """
        if not parameters:
            return False
        
        any_added = False
        for parameter in parameters:
            if parameter and isinstance(parameter, dict):
                if self.add_parameter(parameter, action_name):
                    any_added = True
        return any_added

    def add_directives(self, directives: List[str], action_name: str) -> bool:
        """Add multiple directives to the interaction.

        Bulk convenience method that adds multiple directives with the same action_name.
        Prevents duplicates: no identical directives from the same action are added twice.

        Args:
            directives: List of directive strings to add
            action_name: Class name (camelCase) of the action that added these directives

        Returns:
            True if any directive was added (not a duplicate), False if all were duplicates or empty
        """
        if not directives:
            return False
        
        any_added = False
        for directive in directives:
            if directive:  # Skip empty directives
                if self.add_directive(directive, action_name):
                    any_added = True
        return any_added

    def has_response(self) -> bool:
        """Check if the interaction has a response.

        Returns:
            True if response is set, False otherwise
        """
        return self.response is not None

    def set_response(self, content: str) -> bool:
        """Set the interaction response.

        Args:
            content: Response content

        Returns:
            True if the response was changed, False if it was already set to this value
        """
        if self.response == content:
            return False  # No change, avoid unnecessary saves
        self.response = content
        return True  # Changed

    def get_unexecuted_directives(self) -> List[Dict[str, Any]]:
        """Get directives that have not yet been executed.

        Returns:
            List of unexecuted directive entries (dicts with action_name, content, executed=False)
        """
        return [d for d in self.directives if not d.get("executed", False)]

    def get_executed_directives(self) -> List[Dict[str, Any]]:
        """Get directives that have been executed.

        Returns:
            List of executed directive entries (dicts with action_name, content, executed=True)
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

    def get_directives_by_action(self, action_name: str) -> List[Dict[str, Any]]:
        """Get directives added by a specific action.

        Args:
            action_name: Class name (camelCase) of the action to filter by

        Returns:
            List of directive entries from the specified action
        """
        return [d for d in self.directives if d.get("action_name") == action_name]

    def get_parameters_by_action(self, action_name: str) -> List[Dict[str, Any]]:
        """Get parameters added by a specific action.

        Args:
            action_name: Class name (camelCase) of the action to filter by

        Returns:
            List of parameter entries from the specified action
        """
        return [p for p in self.parameters if p.get("action_name") == action_name]

    def get_events_by_action(self, action_name: str) -> List[Dict[str, Any]]:
        """Get events added by a specific action.

        Args:
            action_name: Class name (camelCase) of the action to filter by

        Returns:
            List of event entries from the specified action
        """
        return [e for e in self.events if e.get("action_name") == action_name]

    def set_to_executed(self, parameters: List[Dict[str, Any]] = [], directives: List[Dict[str, Any]] = []) -> None:
        """Mark directives and parameters as executed.

        Finds matching entries in self.directives and self.parameters by comparing
        action_name and content, then sets executed=True on matching entries in-place.

        Args:
            parameters: Parameter entries to mark as executed
            directives: Directive entries to mark as executed
        """
        # Mark matching directives as executed
        for directive_entry in directives:
            action_name = directive_entry.get("action_name")
            content = directive_entry.get("content")
            if action_name and content:
                for d in self.directives:
                    if d.get("action_name") == action_name and d.get("content") == content:
                        d["executed"] = True

        # Mark matching parameters as executed
        for parameter_entry in parameters:
            action_name = parameter_entry.get("action_name")
            # Match by action_name and a unique identifier if available (e.g., "id" or "condition")
            # If no unique identifier, match by action_name and all other keys
            for p in self.parameters:
                if p.get("action_name") == action_name:
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

    def get_directives(self) -> List[Dict[str, Any]]:
        """Get all directives.

        Returns:
            List of directive entries (dicts with action_name, content, executed)
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
            "session_id": self.session_id,
            "utterance": self.utterance,
            "channel": self.channel,
            "response": self.response,
            "actions": self.actions,
            "directives": self.directives,  # Includes executed status
            "parameters": self.parameters,  # Includes executed status
            "events": self.events,  # Logs - no execution status
            "observability_metrics": self.observability_metrics,
            "interpretation": self.interpretation,
            "anchors": self.anchors,
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

    async def get_user(self) -> Optional["User"]:
        """Get the User node this Interaction belongs to.

        Traverses: Interaction -> Conversation -> User.

        Returns:
            User instance if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation
        from jvagent.memory.user import User

        if not self.conversation_id:
            return None

        conversation = await Conversation.get(self.conversation_id)
        if not conversation:
            return None

        return await conversation.node(direction="in", node=User)

    async def get_conversation(self) -> Optional["Conversation"]:
        """Get the Conversation node this Interaction belongs to.

        Returns:
            Conversation instance if found, None otherwise
        """
        from jvagent.memory.conversation import Conversation

        # Get Conversation node using conversation_id
        if self.conversation_id:
            return await Conversation.get(self.conversation_id)
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
