"""Actions manager node for agent action registration and discovery."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Type, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.core.cache import invalidate_action_cache

logger = logging.getLogger(__name__)


class Actions(Node):
    """Central node for managing agent actions.

    The Actions node manages the registration and discovery of actions for an agent.
    It maintains statistics and provides helper queries, but delegates all lifecycle
    operations (enable, disable, reload) to the Action class itself.

    Attributes:
        registered_count: Number of registered actions
        enabled_count: Number of enabled actions
        _lock: Async lock for thread-safe operations (private, not persisted)
    """

    # Statistics
    registered_count: int = attribute(default=0, description="Number of registered actions")
    enabled_count: int = attribute(default=0, description="Number of enabled actions")

    # Internal lock for thread-safe operations
    _lock: asyncio.Lock = attribute(private=True, default_factory=asyncio.Lock)

    # ============================================================================
    # Action Registration
    # ============================================================================

    async def register_action(self, action: Action, update_if_exists: bool = False) -> bool:
        """Register or update an action with this manager.

        This method includes a safeguard to prevent duplicate actions from being registered
        under the same agent. Actions are uniquely identified by (agent_id, namespace, label).

        This method:
        1. Checks if action already exists (by agent_id, namespace, and label)
        2. If exists and update_if_exists=True: deletes existing action(s) and creates fresh
        3. If exists and update_if_exists=False: skips registration to prevent duplicate
        4. If not exists: creates the action node in the graph
        5. Connects it to this Actions manager node
        6. Calls the action's lifecycle hook:
           - on_register() for new actions (even during update mode)
           - on_reload() only for actions that actually existed before this registration
        7. Updates statistics

        Args:
            action: Action instance to register
            update_if_exists: If True, delete existing action(s) and create fresh; if False, skip if exists

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                # Check if action already exists (uniquely identified by agent_id, namespace, label)
                existing_action = await Action.find_one(
                    {
                        "context.agent_id": action.agent_id,
                        "context.namespace": action.namespace,
                        "context.label": action.label,
                    }
                )

                # Track if action existed before this registration
                # This determines whether to call on_register() or on_reload()
                action_existed_before = existing_action is not None

                if existing_action:
                    if not update_if_exists:
                        # Action exists and we're not updating - reuse existing and return early
                        if not await self.is_connected_to(existing_action):
                            await self.connect(existing_action, direction="both")
                        logger.debug(
                            "Action %s already exists for agent %s (namespace=%s); reused existing node %s",
                            action.label,
                            action.agent_id,
                            action.namespace,
                            existing_action.id,
                        )
                        return True

                    # Update mode: clean up all existing actions (including duplicates)
                    all_existing = await Action.find(
                        {
                            "context.agent_id": action.agent_id,
                            "context.namespace": action.namespace,
                            "context.label": action.label,
                        }
                    )
                    # Delete all existing actions (we'll create fresh)
                    for existing in all_existing:
                        try:
                            if await self.is_connected_to(existing):
                                await self.disconnect(existing)
                            await existing.delete()
                            self.registered_count = max(0, self.registered_count - 1)
                            if existing.enabled:
                                self.enabled_count = max(0, self.enabled_count - 1)
                        except Exception as e:
                            logger.error(
                                f"Error deleting existing action {existing.id}: {e}",
                                exc_info=True,
                            )
                else:
                    # No existing action - check for and clean up any orphaned duplicates
                    all_duplicates = await Action.find(
                        {
                            "context.agent_id": action.agent_id,
                            "context.namespace": action.namespace,
                            "context.label": action.label,
                        }
                    )
                    # Remove any duplicates (shouldn't exist, but handle gracefully)
                    for duplicate in all_duplicates:
                        try:
                            if await self.is_connected_to(duplicate):
                                await self.disconnect(duplicate)
                            await duplicate.delete()
                            self.registered_count = max(0, self.registered_count - 1)
                            if duplicate.enabled:
                                self.enabled_count = max(0, self.enabled_count - 1)
                            logger.warning(
                                "Removed orphaned duplicate action %s (agent_id=%s, namespace=%s, label=%s)",
                                duplicate.id,
                                duplicate.agent_id,
                                duplicate.namespace,
                                duplicate.label,
                            )
                        except Exception as e:
                            logger.error(
                                f"Error removing duplicate action {duplicate.id}: {e}", exc_info=True
                            )

                # Register the new action
                self.registered_count += 1
                if action.enabled:
                    self.enabled_count += 1

                await action.save()

                if not await self.is_connected_to(action):
                    await self.connect(action, direction="both")

                # Post-save validation: check for race condition duplicates
                other_existing = await Action.find_one(
                    {
                        "context.agent_id": action.agent_id,
                        "context.namespace": action.namespace,
                        "context.label": action.label,
                    }
                )

                if other_existing and other_existing.id != action.id:
                    # Race condition: another action was registered between our check and save
                    logger.warning(
                        f"Duplicate action detected after save for {action.namespace}/{action.label} "
                        f"(agent_id={action.agent_id}). Removing duplicate {action.id}, "
                        f"keeping existing {other_existing.id}"
                    )

                    # Clean up the duplicate we just created
                    if await self.is_connected_to(action):
                        await self.disconnect(action)
                    await action.delete()
                    self.registered_count = max(0, self.registered_count - 1)
                    if action.enabled:
                        self.enabled_count = max(0, self.enabled_count - 1)

                    # Ensure connection to the existing action
                    if not await self.is_connected_to(other_existing):
                        await self.connect(other_existing, direction="both")

                    await self.save()
                    return True

                # Call appropriate lifecycle hook with error handling
                # Use on_register() for new actions (even during update mode)
                # Use on_reload() only for actions that actually existed before
                try:
                    if update_if_exists and action_existed_before:
                        # True update: action existed before, use on_reload()
                        await action.on_reload()
                        context_name = "on_reload"
                    else:
                        # New action or fresh install: use on_register()
                        await action.on_register()
                        context_name = "on_register"
                except Exception as e:
                    # Log to console (database logging handled automatically by DBLogHandler)
                    logger.error(
                        f"Error in lifecycle hook for action {action.label}: {e}",
                        exc_info=True,
                        extra={
                            "details": {
                                "agent_id": action.agent_id,
                                "action_class": action.get_class_name(),
                                "action_id": action.id,
                                "action_label": action.label,
                                "context": context_name,
                                "error_code": f"action_{context_name}_error",
                            }
                        }
                    )
                    raise  # Re-raise to be caught by outer handler

                await self.save()
                
                # Invalidate action cache for this agent
                await invalidate_action_cache(action.agent_id)
                
                return True

            except Exception as e:
                logger.error(f"Error registering action {action.label}: {e}", exc_info=True)
                return False

    async def register_actions(
        self, actions: List[Action], update_if_exists: bool = False
    ) -> Dict[str, bool]:
        """Register or update multiple actions.

        Args:
            actions: List of action instances to register
            update_if_exists: If True, update existing actions; if False, skip if exists

        Returns:
            Dictionary mapping action labels to registration status
        """
        results = {}
        registered_actions = []  # Track actions that were actually registered (not duplicates)

        for action in actions:
            success = await self.register_action(action, update_if_exists=update_if_exists)
            results[action.label] = success

            # Only track as registered if it was successful AND we need to verify it wasn't a duplicate
            if success:
                # Verify the action still exists and is connected (not deleted as duplicate)
                try:
                    existing = await Action.find_one({
                        "context.agent_id": action.agent_id,
                        "context.namespace": action.namespace,
                        "context.label": action.label,
                    })
                    if existing and existing.id == action.id:
                        # This is the action we registered (not a duplicate)
                        registered_actions.append(existing)
                except Exception:
                    # If we can't verify, skip post_register to be safe
                    pass

        # Call post_register only on actions that were actually registered (not duplicates)
        for action in registered_actions:
            try:
                await action.post_register()
            except Exception as e:
                # Log to console (database logging handled automatically by DBLogHandler)
                logger.error(
                    f"Error in post_register for {action.label}: {e}",
                    exc_info=True,
                    extra={
                        "details": {
                            "agent_id": action.agent_id,
                            "action_class": action.get_class_name(),
                            "action_id": action.id,
                            "action_label": action.label,
                            "context": "post_register",
                            "error_code": "action_post_register_error",
                        }
                    }
                )

        return results

    async def deregister_action(self, action_id: str) -> bool:
        """Deregister an action from this manager.

        This method performs complete cleanup:
        1. Unregisters all endpoints associated with the action
        2. Unloads action-specific modules (if safe)
        3. Calls the action's on_deregister() lifecycle hook
        4. Updates statistics
        5. Deletes the action node (removes graph edges)

        Args:
            action_id: ID of the action to deregister

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                # Get the action
                action = await Action.get(action_id)
                if not action:
                    return False

                # Step 1: Unregister endpoints associated with this action
                try:
                    endpoints_unregistered = await action._unregister_endpoints()
                    if endpoints_unregistered > 0:
                        logger.debug(
                            f"Unregistered {endpoints_unregistered} endpoint(s) for action {action_id}"
                        )
                except Exception as e:
                    logger.warning(f"Error unregistering endpoints for action {action_id}: {e}")

                # Step 2: Unload action-specific modules (if safe)
                try:
                    modules_unloaded = await action._unload_action_modules()
                    if modules_unloaded > 0:
                        logger.debug(
                            f"Unloaded {modules_unloaded} module(s) for action {action_id}"
                        )
                except Exception as e:
                    logger.warning(f"Error unloading modules for action {action_id}: {e}")

                # Step 3: Call lifecycle hook (allows action-specific cleanup)
                try:
                    await action.on_deregister()
                except Exception as e:
                    # Log to console (database logging handled automatically by DBLogHandler)
                    logger.error(
                        f"Error in on_deregister for action {action_id}: {e}",
                        exc_info=True,
                        extra={
                            "details": {
                                "agent_id": action.agent_id,
                                "action_class": action.get_class_name(),
                                "action_id": action.id,
                                "action_label": action.label,
                                "context": "on_deregister",
                                "error_code": "action_deregister_error",
                            }
                        }
                    )
                    # Continue with deregistration even if hook fails

                # Step 4: Update statistics
                self.registered_count = max(0, self.registered_count - 1)
                if action.enabled:
                    self.enabled_count = max(0, self.enabled_count - 1)

                # Step 5: Delete the action (this also removes edges)
                await action.delete()
                await self.save()

                # Invalidate action cache for this agent
                await invalidate_action_cache(action.agent_id)

                return True

            except Exception as e:
                logger.error(f"Error deregistering action {action_id}: {e}", exc_info=True)
                return False

    # ============================================================================
    # Action Query - Entity-Centric
    # ============================================================================

    async def get_actions(
        self, enabled_only: bool = False, entity: Optional[Union[Type[Action], str]] = None
    ) -> List[Action]:
        """Get all actions for this agent using node traversal.

        Uses self.nodes() to get all connected Action nodes (including subclasses).
        Optionally filters by enabled status and/or specific action entity type.

        Args:
            enabled_only: If True, only return enabled actions
            entity: Optional action type to filter by (e.g., InteractAction, "InteractAction").
                   If None, returns all Action types. If specified, returns only that type
                   and its subclasses.

        Returns:
            List of action instances
        """
        try:
            # Determine node filter - use entity if provided, otherwise default to Action
            node_filter: Union[Type[Action], str] = entity if entity is not None else Action

            # Build kwargs for property filtering
            kwargs = {}
            if enabled_only:
                kwargs["enabled"] = True

            return await self.nodes(node=node_filter, **kwargs)

        except Exception as e:
            logger.error(f"Error getting actions: {e}", exc_info=True)
            return []

    async def get_all_actions(self, enabled_only: bool = False, entity: Optional[Union[Type[Action], str]] = None) -> List[Any]:
        """Get all actions for this agent, including actions attached to actions (subactions).

        This recursively traverses the action graph to find all actions.

        Args:
            enabled_only: If True, only return enabled actions

        Returns:
            Flat list of all Action instances found in the hierarchy
        """
        # Get top-level actions
        top_level_actions = await self.get_actions(enabled_only=enabled_only, entity=entity)

        all_actions = []
        processed_ids = set()

        # Stack for recursion (bfs/dfs)
        stack = list(top_level_actions)

        while stack:
            current_action_node = stack.pop(0)

            if current_action_node.id in processed_ids:
                continue

            processed_ids.add(current_action_node.id)
            all_actions.append(current_action_node)

            # Get subactions for this action
            subactions = await current_action_node.nodes(node=entity)
            if not subactions:
                continue

            # Add to stack
            stack.extend(subactions)
        return all_actions

    async def get_action_by_label(self, label: str) -> Optional[Action]:
        """Get an action by its label using entity-centric queries.

        Args:
            label: Action label to search for

        Returns:
            Action instance if found, None otherwise
        """
        try:
            # Get agent_id
            connected_nodes = await self.nodes()
            agent = None
            for node in connected_nodes:
                from jvagent.core.agent import Agent

                if isinstance(node, Agent):
                    agent = node
                    break

            if not agent:
                return None

            # Use entity-centric find_one
            return await Action.find_one({"context.agent_id": agent.id, "context.label": label})

        except Exception as e:
            logger.error(f"Error getting action by label {label}: {e}", exc_info=True)
            return None

    async def get_action_info(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about an action.

        Args:
            action_id: ID of the action

        Returns:
            Dictionary with action information, or None if not found
        """
        try:
            action = await Action.get(action_id)
            if not action:
                return None

            return await action.to_dict()

        except Exception as e:
            logger.error(f"Error getting action info for {action_id}: {e}", exc_info=True)
            return None

    async def list_actions(self) -> List[Dict[str, Any]]:
        """List all actions with their information.

        Returns:
            List of action information dictionaries
        """
        actions = await self.get_actions()
        return [await action.to_dict() for action in actions]

    # ============================================================================
    # Statistics Management
    # ============================================================================

    async def update_statistics(self) -> None:
        """Update action statistics by querying actual action states.

        Call this periodically to ensure statistics are accurate.
        """
        try:
            actions = await self.get_actions()

            self.registered_count = len(actions)
            self.enabled_count = sum(1 for a in actions if a.enabled)

            await self.save()

        except Exception as e:
            logger.error(f"Error updating statistics: {e}", exc_info=True)

    # ============================================================================
    # Health and Maintenance
    # ============================================================================

    async def pulse_all(self) -> Dict[str, Any]:
        """Run pulse on all enabled actions.

        Returns:
            Dictionary mapping action IDs to pulse results
        """
        actions = await self.get_actions(enabled_only=True)
        results = {}

        for action in actions:
            try:
                result = await action.pulse()
                results[action.id] = result
            except Exception as e:
                results[action.id] = {"error": str(e)}

        return results

    async def healthcheck_all(self) -> Dict[str, Any]:
        """Run healthcheck on all actions.

        Returns:
            Dictionary mapping action IDs to healthcheck results
        """
        actions = await self.get_actions()
        results = {}

        for action in actions:
            try:
                result = await action.healthcheck()
                results[action.id] = result
            except Exception as e:
                results[action.id] = {"healthy": False, "error": str(e)}

        return results
