"""InteractWalker for traversing InteractActions in the interact subsystem.

This module provides the InteractWalker that serves as the common entry point
for agent interactions, replacing the PersonaAction interact endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from jvspatial.core import Walker, on_visit
from jvagent.memory.interaction import Interaction
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.actions import Actions
    from jvagent.core.agent import Agent
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.manager import Memory
    from jvagent.memory.user import User

logger = logging.getLogger(__name__)


class InteractWalker(Walker):
    """Walker that traverses InteractActions for agent interactions.

    InteractWalker is the common entry point for agent interactions. It:
    - Handles user/conversation resolution
    - Creates Interaction node
    - Traverses from Agent -> Actions -> InteractActions
    - Executes top-level InteractActions in weight order (from Actions node)
    - Traverses sub-actions in graph-based arrangement (weight not considered)
    - Provides helper methods to record and remove executed actions on the active interaction

    Usage:
        The walker should be spawned directly on the Agent node:
            await walker.spawn(agent)

        This skips the Root -> Agent traversal and starts directly where needed.

    Weight Ordering:
        Weight is only applied at the top tier of the InteractAction graph when
        launching from the Actions node. This is because top-level InteractActions
        are connected in a flat arrangement and there may be multiple top-level
        actions that need ordering.

        Sub-actions (InteractActions connected to other InteractActions) are
        traversed in graph-based arrangement without weight consideration, as
        one InteractAction should lead to another based on the graph structure.
    """

    # Walker state
    agent_id: str = ""
    utterance: str = ""
    channel: str = "default"
    data: Dict[str, Any] = {}
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    interaction: Optional["Interaction"] = None
    stream_mode: bool = False
    response_bus: Optional[Any] = None
    _current_action: Optional["InteractAction"] = None  # Track current executing action for convenience methods
    _skip_current_action_record: bool = False  # Allow actions to opt-out of being recorded as executed

    async def record_action_execution(self, action_name: Optional[str] = None) -> Optional[str]:
        """Record an executed action on the active interaction.

        If called from within an InteractAction execution and no action_name is
        provided, the current action's class name is inferred automatically.

        Args:
            action_name: Optional explicit action class name to record.

        Returns:
            The action name recorded, or None if no interaction or action was available.
        """
        if not self.interaction:
            return None

        name = action_name
        if not name and self._current_action:
            try:
                name = self._current_action.get_class_name()
            except Exception:
                name = None

        if not name:
            return None

        self.interaction.record_action_execution(name)
        await self.interaction.save()
        return name

    async def unrecord_action_execution(self, action_name: Optional[str] = None) -> Optional[str]:
        """Remove or prevent recording of a previously/currently executed action.

        If called from within an InteractAction execution and no action_name is
        provided, the current action's class name is inferred automatically.

        Behavior:
        - If the action has already been recorded on the interaction, it is removed.
        - If the action has not yet been recorded for the current execution, it
          marks the current action so it will NOT be recorded after execute().

        Args:
            action_name: Optional explicit action class name to remove.

        Returns:
            The action name removed or skipped, or None if no interaction or action was available.
        """
        if not self.interaction:
            return None

        name = action_name
        if not name and self._current_action:
            try:
                name = self._current_action.get_class_name()
            except Exception:
                name = None

        if not name:
            return None

        # If this refers to the currently executing action, mark it so it won't be recorded
        if self._current_action:
            try:
                current_name = self._current_action.get_class_name()
            except Exception:
                current_name = None
            if current_name and current_name == name:
                self._skip_current_action_record = True

        # If it was already recorded, remove it from the interaction history
        if name in self.interaction.actions:
            self.interaction.unrecord_action_execution(name)
            await self.interaction.save()

        return name

    @on_visit("Agent")
    async def on_agent(self, here: "Agent") -> None:
        """Visit Agent node and walk to Actions node.

        Args:
            here: The Agent node being visited
        """
        # Initialize interaction if not already done
        if not self.interaction:
            # Get memory from agent
            memory = await here.get_memory()
            if not memory:
                await self.report(
                    {"error": "Agent has no Memory node"}
                )
                return

            # Get ResponseBus instance from app
            from jvagent.core.app import App
            app = await App.get()
            if app:
                self.response_bus = await app.get_response_bus()
            else:
                logger.warning("App node not found - ResponseBus unavailable")

            # Resolve user and conversation via memory.get_session()
            try:
                user, conversation, resolved_user_id, resolved_session_id, new_user = (
                    await memory.get_session(
                        user_id=self.user_id,
                        session_id=self.session_id,
                        channel=self.channel,
                    )
                )
                self.user_id = resolved_user_id
                self.session_id = resolved_session_id
                self.new_user = new_user

                # Create interaction
                from jvagent.memory.interaction import Interaction
                from jvagent.action.model.context import set_interaction_id

                self.interaction = await conversation.create_interaction(
                    utterance=self.utterance,
                    channel=self.channel,
                    session_id=self.session_id,
                )
                
                # Set interaction_id in context for automatic observability
                set_interaction_id(self.interaction.id)

                # Store data on interaction if provided
                if self.data:
                    # Store data in interaction context (if supported)
                    pass  # Interaction may need data attribute

                await self.report(
                    {
                        "interaction_created": {
                            "interaction_id": self.interaction.id,
                            "user_id": self.user_id,
                            "session_id": self.session_id,
                        }
                    }
                )
            except Exception as e:
                await self.report(
                    {"error": f"Failed to initialize interaction: {e}"}
                )
                logger.error(f"Error initializing interaction: {e}", exc_info=True)
                return

        # Get Actions node
        actions_node = await here.get_actions_manager()
        if not actions_node:
            await self.report({"error": "Agent has no Actions node"})
            return

        # Walk to Actions node
        await self.visit(actions_node)

    @on_visit("Actions")
    async def on_actions(self, here: Any) -> None:
        """Visit Actions node and queue top-level InteractActions for traversal.

        Gets all connected InteractActions, filters to enabled ones, applies routing
        filter if InteractRouter has executed, sorts by weight (weight is only considered
        at this top tier), and queues them for traversal. The @on_visit("InteractAction")
        handler will process each action with depth-first traversal of sub-actions
        (without weight consideration).

        Args:
            here: The Actions node being visited
        """
        from jvagent.action.interact.base import InteractAction
        from jvagent.action.base import Action

        # Debug: Get all actions first to see what's available
        all_actions = await here.nodes(node=Action)
        action_info = []
        for a in all_actions:
            try:
                class_name = a.get_class_name()
                action_info.append(f"{a.label} ({class_name}, enabled={a.enabled})")
            except Exception as e:
                action_info.append(f"{a.label} (error getting class name: {e})")
        logger.debug(
            f"InteractWalker: Found {len(all_actions)} total actions connected to Actions node: {action_info}"
        )

        # Get all enabled InteractActions (forward direction from Actions node)
        # Use class type instead of string to match by isinstance() (includes subclasses like InteractRouter)
        # Filter by enabled=True directly in the query using kwargs
        enabled_actions: List[InteractAction] = await here.nodes(
            node=InteractAction, enabled=True
        )

        if not enabled_actions:
            # Debug: Check if there are any InteractActions at all (even disabled)
            all_interact_actions = await here.nodes(node=InteractAction)
            logger.warning(
                f"InteractWalker: No enabled InteractActions found. "
                f"Total InteractActions: {len(all_interact_actions)}, "
                f"Enabled: {[a.label for a in all_interact_actions if a.enabled]}, "
                f"Disabled: {[a.label for a in all_interact_actions if not a.enabled]}"
            )
            await self.report({"info": "No enabled InteractActions found"})
            return

        # Apply routing filter if InteractRouter has executed
        # Check if InteractRouter has run by checking if interpretation exists
        # InteractRouter always sets interpretation when it runs (even if empty anchors)
        if self.interaction and self.interaction.interpretation:
            logger.debug(
                f"InteractWalker: InteractRouter has executed. "
                f"Interpretation: {self.interaction.interpretation[:100]}, "
                f"Anchors: {self.interaction.anchors}"
            )
            enabled_actions = await self._filter_by_routing(enabled_actions)
        else:
            logger.debug("InteractWalker: InteractRouter has not executed yet, allowing all actions")

        # Sort by weight (negative first, then ascending)
        # Actions with same weight maintain descriptor order (stable sort)
        sorted_actions = sorted(enabled_actions, key=lambda a: a.weight)

        await self.report(
            {
                "interact_actions_found": {
                    "count": len(sorted_actions),
                    "actions": [a.label for a in sorted_actions],
                }
            }
        )

        # Queue actions for traversal (walker will process them via @on_visit)
        await self.visit(sorted_actions)

    @on_visit(InteractAction)
    async def on_interact_action(self, here: "InteractAction") -> None:
        """Visit an InteractAction node: perform routing checks, then traverse sub-actions.

        This method is automatically called when the walker visits an InteractAction.
        It:
        1. Checks if action should execute based on routing (if InteractRouter has run)
        2. Executes the action's execute() method
        3. Finds connected InteractActions (sub-actions) in graph-based arrangement
        4. Queues them for depth-first traversal (weight is NOT considered for sub-actions)
        5. The walker continues naturally to process queued actions

        Note:
            Sub-actions are traversed in graph-based arrangement without weight
            consideration. Weight is only applied at the top tier when launching
            from the Actions node.

        Args:
            here: The InteractAction node being visited
        """
        if not here.enabled:
            await self.report(
                {
                    "action_skipped": {
                        "action": here.label,
                        "weight": here.weight,
                        "reason": "action is disabled",
                    }
                }
            )
            return

        # Check routing if InteractRouter has executed
        # If InteractRouter has executed (interpretation is set), check if this action should execute
        if self.interaction and self.interaction.interpretation:
            routed_entity_names = set(self.interaction.anchors) if self.interaction.anchors else set()
            action_entity_name = here.get_class_name()

            # Skip if not in routed entities (unless it's InteractRouter itself, which must execute first)
            if action_entity_name not in routed_entity_names and action_entity_name != "InteractRouter":
                logger.debug(
                    f"InteractWalker: Skipping {action_entity_name} (label: {here.label}) - "
                    f"not in routed entities: {routed_entity_names}"
                )
                await self.report(
                    {
                        "action_skipped": {
                            "action": here.label,
                            "weight": here.weight,
                            "reason": f"not routed (routed entities: {list(routed_entity_names)})",
                        }
                    }
                )
                return

        try:
            # Store current action for convenience methods and reset skip flag
            self._current_action = here
            self._skip_current_action_record = False

            # Record action execution BEFORE execution to ensure it appears before
            # any actions it calls (like PersonaAction). This preserves the call order
            # in the actions list - the calling action appears before the called action.
            if self.interaction and not self._skip_current_action_record:
                await self.record_action_execution()

            # Execute the action
            # Note: 'here' is the node (self from node's perspective), 'self' is the walker (visitor)
            await here.execute(self)

            await self.report(
                {
                    "action_executed": {
                        "action": here.label,
                        "weight": here.weight,
                        "class": here.get_class_name(),
                    }
                }
            )

            # Find connected enabled InteractActions (sub-actions) for depth-first traversal
            # Using forward direction from the current InteractAction
            # Use class type instead of string to match by isinstance() (includes subclasses like InteractRouter)
            # Filter by enabled=True directly in the query using kwargs
            # Note: Routing filtering is NOT applied to sub-actions, only to top-level actions
            enabled_sub_actions: List["InteractAction"] = await here.nodes(
                node=InteractAction, enabled=True
            )

            if enabled_sub_actions:
                # Sub-actions are traversed in graph-based arrangement (no weight sorting)
                # Weight is only considered at the top tier from Actions node
                await self.report(
                    {
                        "sub_actions_found": {
                            "parent": here.label,
                            "count": len(enabled_sub_actions),
                            "actions": [a.label for a in enabled_sub_actions],
                        }
                    }
                )

                # Queue sub-actions at the front for depth-first traversal
                # This ensures sub-actions are processed before sibling actions
                # Traversal follows graph structure, not weight ordering
                await self.add_next(enabled_sub_actions)

        except Exception as e:
            logger.error(
                f"Error processing InteractAction {here.label}: {e}",
                exc_info=True,
            )
            await self.report(
                {
                    "error": f"Failed to process {here.label}",
                    "exception": str(e),
                }
            )
            # Continue to next action (don't raise, let walker continue)
        finally:
            # Always clear current action and skip flag after execution
            self._current_action = None
            self._skip_current_action_record = False

    async def _filter_by_routing(
        self, actions: List["InteractAction"]
    ) -> List["InteractAction"]:
        """Filter InteractActions based on routing results from InteractRouter.

        If InteractRouter has executed (indicated by interpretation being set), only
        actions whose class/entity names are in the interaction.anchors list (plus
        exceptions from InteractRouter) will be allowed. The order of actions is preserved
        (filtering only, no reordering).

        Args:
            actions: List of InteractActions to filter

        Returns:
            Filtered list of InteractActions that match routing (anchors + exceptions)
        """
        if not self.interaction:
            return actions

        # If InteractRouter has executed (interpretation is set), apply strict filtering
        # Even if anchors list is empty, only allow exceptions (if any)
        # Anchors list already includes both routed entities and exceptions from InteractRouter
        routed_entity_names = set(self.interaction.anchors) if self.interaction.anchors else set()

        logger.debug(
            f"InteractWalker: Applying routing filter. "
            f"Interpretation: {self.interaction.interpretation[:100] if self.interaction.interpretation else None}, "
            f"Routed entities: {routed_entity_names}, "
            f"Total actions to filter: {len(actions)}"
        )

        filtered: List["InteractAction"] = []

        for action in actions:
            # Check if this action should be allowed
            if self._should_allow_action(action, routed_entity_names):
                filtered.append(action)

        logger.info(
            f"InteractWalker: Routing filter applied. "
            f"Original: {len(actions)}, Filtered: {len(filtered)}, "
            f"Routed entities: {list(routed_entity_names)}"
        )

        if len(filtered) < len(actions):
            await self.report(
                {
                    "routing_filter_applied": {
                        "original_count": len(actions),
                        "filtered_count": len(filtered),
                        "routed_entities": list(routed_entity_names),
                    }
                }
            )

        return filtered

    def _should_allow_action(
        self, action: "InteractAction", routed_entity_names: set
    ) -> bool:
        """Check if an action should be allowed based on routing.

        When InteractRouter has executed, an action is allowed only if:
        1. Its class/entity name is in the routed entity names (anchors + exceptions)

        Args:
            action: The InteractAction to check
            routed_entity_names: Set of entity names that were routed to (anchors + exceptions)

        Returns:
            True if action should be allowed, False otherwise
        """
        # Get the action's entity name (class name)
        action_entity_name = action.get_class_name()

        # Check if this action's entity name is in the routed anchors/exceptions
        is_allowed = action_entity_name in routed_entity_names

        logger.debug(
            f"InteractWalker: Checking {action_entity_name} (label: {action.label}) - "
            f"Allowed: {is_allowed}, Routed entities: {routed_entity_names}"
        )

        return is_allowed

    async def add_directives(self, directives: List[str]) -> None:
        """Add multiple directives to the interaction with current action name.

        Bulk convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved once after adding all directives.

        Args:
            directives: List of directive strings to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError("add_directives() must be called from within InteractAction.execute()")

        if not directives:
            return

        action_name = self._current_action.get_class_name()
        for directive in directives:
            if directive:  # Skip empty directives
                self.interaction.add_directive(directive, action_name)
        
        await self.interaction.save()

    async def add_directive(self, directive: str) -> None:
        """Add a directive to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the directive.

        Args:
            directive: Directive string to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        await self.add_directives([directive])

    async def add_event(self, event: str) -> None:
        """Add an event to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the event.

        Args:
            event: Event string to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError("add_event() must be called from within InteractAction.execute()")

        action_name = self._current_action.get_class_name()
        self.interaction.add_event(event, action_name)
        await self.interaction.save()

    async def add_parameters(self, parameters: List[Dict[str, Any]]) -> None:
        """Add multiple parameters to the interaction with current action name.

        Bulk convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved once after adding all parameters.

        Args:
            parameters: List of parameter dictionaries to add (each should have 'condition' and 'response' keys)

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError("add_parameters() must be called from within InteractAction.execute()")

        if not parameters:
            return

        action_name = self._current_action.get_class_name()
        for parameter in parameters:
            if parameter and isinstance(parameter, dict):
                self.interaction.add_parameter(parameter, action_name)
            elif parameter:
                logger.warning(f"add_parameters: Skipping invalid parameter type: {type(parameter)}, value: {parameter}")
        
        await self.interaction.save()

    async def add_parameter(self, parameter: Dict[str, Any]) -> None:
        """Add a parameter to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the parameter.

        Args:
            parameter: Parameter data (id, condition, response, etc.)

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        await self.add_parameters([parameter])
