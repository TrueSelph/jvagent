"""Actions manager node for agent action registration and discovery."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Type, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

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
    registered_count: int = attribute(
        default=0, description="Number of registered actions"
    )
    enabled_count: int = attribute(default=0, description="Number of enabled actions")

    # Internal lock for thread-safe operations
    _lock: asyncio.Lock = attribute(private=True, default_factory=asyncio.Lock)

    # ============================================================================
    # Action Registration
    # ============================================================================

    async def register_action(
        self,
        action: Action,
        update_mode: Optional[str] = None,
        property_overrides: Optional[set] = None,
    ) -> bool:
        """Register or update an action with this manager.

        Actions are uniquely identified by (agent_id, namespace, label).

        This method:
        1. Enforces singleton constraint for singleton action types
        2. Checks if action already exists (by agent_id, namespace, label)
        3. If exists and update_mode=None: reuses existing node (no-op)
        4. If exists and update_mode="merge": updates metadata in place, calls on_reload()
        5. If exists and update_mode="source": deletes existing and creates fresh
        6. If not exists: saves and connects the action, calls on_register()

        Prior to calling this method during bootstrap, _reconcile_actions() guarantees
        a clean slate — stale and duplicate nodes have already been removed — so no
        defensive duplicate-detection is performed here.

        Args:
            action: Action instance to register (fresh from source)
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip
            property_overrides: Kept for API compatibility; unused internally.

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                # Singleton enforcement: reject duplicate registration of singleton action types
                if action.is_singleton:
                    archetype = action.metadata.get("class", action.get_class_name())
                    existing_singleton = await Action.find_one(
                        {
                            "context.agent_id": action.agent_id,
                            "context.metadata.class": archetype,
                        }
                    )
                    if existing_singleton:
                        if (
                            existing_singleton.namespace != action.namespace
                            or existing_singleton.label != action.label
                        ):
                            logger.warning(
                                "Rejected duplicate singleton action: %s (archetype=%s) already "
                                "registered for agent %s. Only one instance per agent allowed.",
                                action.label,
                                archetype,
                                action.agent_id,
                            )
                            return False

                # Check if action already exists
                existing_action = await Action.find_one(
                    {
                        "context.agent_id": action.agent_id,
                        "context.namespace": action.namespace,
                        "context.label": action.label,
                    }
                )

                action_existed_before = existing_action is not None

                if existing_action:
                    if update_mode is None:
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

                    if update_mode == "merge":
                        return await self._merge_existing_action(
                            existing_action, action, property_overrides
                        )

                    # source mode: delete existing, create fresh
                    if await self.is_connected_to(existing_action):
                        await self.disconnect(existing_action)
                    await existing_action.delete(cascade=True)

                self.registered_count += 1
                if action.enabled:
                    self.enabled_count += 1

                await action.save()

                if not await self.is_connected_to(action):
                    await self.connect(action, direction="both")

                context_name = (
                    "on_reload"
                    if (update_mode is not None and action_existed_before)
                    else "on_register"
                )
                try:
                    if update_mode is not None and action_existed_before:
                        await action.on_reload()
                    else:
                        await action.on_register()
                except Exception as e:
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
                        },
                    )
                    raise

                await self.save()

                from jvagent.core.cache import invalidate_action_cache

                await invalidate_action_cache(action.agent_id)

                return True

            except Exception as e:
                logger.error(
                    f"Error registering action {action.label}: {e}", exc_info=True
                )
                return False

    async def _merge_existing_action(
        self,
        existing_action: Action,
        source_action: Action,
        property_overrides: Optional[set] = None,
    ) -> bool:
        """Update an existing action node in place (non-destructive merge).

        Preserves the existing node's identity, graph connections, child nodes,
        and all DB property values. Only metadata is updated from source to reflect
        current code state (module paths, version, etc.).

        Args:
            existing_action: The DB-persisted action node to update
            source_action: Fresh action instance from source (used for metadata only)
            property_overrides: Unused; kept for API compatibility.

        Returns:
            True if successful, False otherwise
        """
        try:
            existing_action.metadata = source_action.metadata

            await existing_action.save()

            if not await self.is_connected_to(existing_action):
                await self.connect(existing_action, direction="both")

            try:
                await existing_action.on_reload()
            except Exception as e:
                logger.error(
                    f"Error in on_reload for action {existing_action.label}: {e}",
                    exc_info=True,
                    extra={
                        "details": {
                            "agent_id": existing_action.agent_id,
                            "action_class": existing_action.get_class_name(),
                            "action_id": existing_action.id,
                            "action_label": existing_action.label,
                            "context": "on_reload",
                            "error_code": "action_on_reload_error",
                        }
                    },
                )
                raise

            await self.save()

            from jvagent.core.cache import invalidate_action_cache

            await invalidate_action_cache(existing_action.agent_id)

            logger.debug(
                "Merged action %s for agent %s (preserved node %s)",
                existing_action.label,
                existing_action.agent_id,
                existing_action.id,
            )
            return True

        except Exception as e:
            logger.error(
                f"Error merging action {existing_action.label}: {e}", exc_info=True
            )
            return False

    async def register_actions(
        self, actions: List[Action], update_mode: Optional[str] = None
    ) -> Dict[str, bool]:
        """Register or update multiple actions.

        Args:
            actions: List of action instances to register
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip

        Returns:
            Dictionary mapping action labels to registration status
        """
        results: Dict[str, bool] = {}
        registered_actions: List[Action] = []

        for action in actions:
            overrides = getattr(action, "_property_override_keys", None)
            success = await self.register_action(
                action,
                update_mode=update_mode,
                property_overrides=overrides,
            )
            results[action.label] = success
            if success:
                registered_actions.append(action)

        # Call post_register for every successfully registered action
        for action in registered_actions:
            try:
                await action.post_register()
            except Exception as e:
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
                    },
                )

        return results

    async def deregister_action(self, action_id: str) -> bool:
        """Deregister an action from this manager.

        Performs complete cleanup in order:
        1. Unregisters all endpoints associated with the action
        2. Unloads action-specific modules (if safe)
        3. Calls on_deregister() lifecycle hook
        4. Disconnects from Actions manager
        5. Deletes the action node (cascade-removes child nodes)

        Statistics (registered_count, enabled_count) are updated here for standalone
        calls.  When called from _reconcile_actions, the caller resets them from
        ground truth after all removals are complete.

        Args:
            action_id: ID of the action to deregister

        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                action = await Action.get(action_id)
                if not action:
                    return False

                try:
                    endpoints_unregistered = await action._unregister_endpoints()
                    if endpoints_unregistered > 0:
                        logger.debug(
                            f"Unregistered {endpoints_unregistered} endpoint(s) for action {action_id}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Error unregistering endpoints for action {action_id}: {e}"
                    )

                try:
                    modules_unloaded = await action._unload_action_modules()
                    if modules_unloaded > 0:
                        logger.debug(
                            f"Unloaded {modules_unloaded} module(s) for action {action_id}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Error unloading modules for action {action_id}: {e}"
                    )

                try:
                    await action.on_deregister()
                except Exception as e:
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
                        },
                    )

                was_enabled = action.enabled
                agent_id = action.agent_id

                if await self.is_connected_to(action):
                    await self.disconnect(action)

                await action.delete(cascade=True)

                self.registered_count = max(0, self.registered_count - 1)
                if was_enabled:
                    self.enabled_count = max(0, self.enabled_count - 1)
                await self.save()

                from jvagent.core.cache import invalidate_action_cache

                await invalidate_action_cache(agent_id)

                return True

            except Exception as e:
                logger.error(
                    f"Error deregistering action {action_id}: {e}", exc_info=True
                )
                return False

    # ============================================================================
    # Action Query - Entity-Centric
    # ============================================================================

    async def get_actions(
        self,
        enabled_only: bool = False,
        entity: Optional[Union[Type[Action], str]] = None,
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
            node_filter: Union[Type[Action], str] = (
                entity if entity is not None else Action
            )

            # Build kwargs for property filtering
            kwargs = {}
            if enabled_only:
                kwargs["enabled"] = True

            return await self.nodes(node=node_filter, **kwargs)

        except Exception as e:
            logger.error(f"Error getting actions: {e}", exc_info=True)
            return []

    async def get_all_actions(
        self,
        enabled_only: bool = False,
        entity: Optional[Union[Type[Action], str]] = None,
    ) -> List[Any]:
        """Get all actions for this agent, including actions attached to actions (subactions).

        This recursively traverses the action graph to find all actions.

        Args:
            enabled_only: If True, only return enabled actions

        Returns:
            Flat list of all Action instances found in the hierarchy
        """
        # Get top-level actions
        top_level_actions = await self.get_actions(
            enabled_only=enabled_only, entity=entity
        )

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
            return await Action.find_one(
                {"context.agent_id": agent.id, "context.label": label}
            )

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
            logger.error(
                f"Error getting action info for {action_id}: {e}", exc_info=True
            )
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
