"""Actions manager node for agent action registration and discovery."""

import logging
from typing import Any, Dict, Set, List, Callable, Optional
import asyncio

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

from jvagent.action.action import Action

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
        6. Calls the action's on_register() or on_reload() lifecycle hook
        7. Updates statistics
        
        Args:
            action: Action instance to register
            update_if_exists: If True, delete existing action(s) and create fresh; if False, skip if exists
            
        Returns:
            True if successful, False otherwise
        """
        async with self._lock:
            try:
                # SAFEGUARD: Check if action already exists to prevent duplicates
                # Query by agent_id, namespace, and label - these uniquely identify an action
                existing_actions = await Action.find({
                    "context.agent_id": action.agent_id,
                    "context.namespace": action.namespace,
                    "context.label": action.label
                })
                
                # Always keep at most one existing action when duplicates are present
                existing_action = existing_actions[0] if existing_actions else None
                # Remove any duplicates beyond the primary
                for duplicate in existing_actions[1:]:
                    try:
                        if await self.is_connected_to(duplicate):
                            await self.disconnect(duplicate)
                        await duplicate.delete()
                        self.registered_count = max(0, self.registered_count - 1)
                        if duplicate.enabled:
                            self.enabled_count = max(0, self.enabled_count - 1)
                        logger.warning(
                            "Removed duplicate action %s (agent_id=%s, namespace=%s, label=%s)",
                            duplicate.id,
                            duplicate.agent_id,
                            duplicate.namespace,
                            duplicate.label,
                        )
                    except Exception as e:
                        logger.error(f"Error removing duplicate action {duplicate.id}: {e}", exc_info=True)
                
                if existing_action:
                    if not update_if_exists:
                        # Skip creating a new node and ensure connection remains intact
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
                    
                    # Update mode: delete the remaining action before creating fresh
                    try:
                        if await self.is_connected_to(existing_action):
                            await self.disconnect(existing_action)
                        await existing_action.delete()
                        self.registered_count = max(0, self.registered_count - 1)
                        if existing_action.enabled:
                            self.enabled_count = max(0, self.enabled_count - 1)
                    except Exception as e:
                        logger.error(f"Error deleting existing action {existing_action.id}: {e}", exc_info=True)
                
                # Register the action (either brand new or after update cleanup)
                self.registered_count += 1
                if action.enabled:
                    self.enabled_count += 1
                
                await action.save()
                
                if not await self.is_connected_to(action):
                    await self.connect(action, direction="both")
                
                if existing_action:
                    await action.on_reload()
                else:
                    await action.on_register()
                
                # Save statistics
                await self.save()
                
                return True
                
            except Exception as e:
                logger.error(f"Error registering action {action.label}: {e}", exc_info=True)
                return False
    
    async def register_actions(self, actions: List[Action], update_if_exists: bool = False) -> Dict[str, bool]:
        """Register or update multiple actions.
        
        Args:
            actions: List of action instances to register
            update_if_exists: If True, update existing actions; if False, skip if exists
            
        Returns:
            Dictionary mapping action labels to registration status
        """
        results = {}
        
        for action in actions:
            success = await self.register_action(action, update_if_exists=update_if_exists)
            results[action.label] = success
        
        # Call post_register on all successfully registered actions
        for action in actions:
            if results.get(action.label):
                try:
                    await action.post_register()
                except Exception as e:
                    logger.error(f"Error in post_register for {action.label}: {e}", exc_info=True)
        
        return results
    
    async def deregister_action(self, action_id: str) -> bool:
        """Deregister an action from this manager.
        
        This method:
        1. Calls the action's on_deregister() lifecycle hook
        2. Removes connections
        3. Deletes the action node
        4. Updates statistics
        
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
                
                # Call lifecycle hook
                await action.on_deregister()
                
                # Update statistics
                self.registered_count = max(0, self.registered_count - 1)
                if action.enabled:
                    self.enabled_count = max(0, self.enabled_count - 1)
                
                # Delete the action (this also removes edges)
                await action.delete()
                await self.save()
                
                return True
                
            except Exception as e:
                logger.error(f"Error deregistering action {action_id}: {e}", exc_info=True)
                return False
    
    # ============================================================================
    # Action Query - Entity-Centric
    # ============================================================================
    
    async def get_actions(self, enabled_only: bool = False) -> List[Action]:
        """Get all actions for this agent using entity-centric queries.
        
        Uses Action.find() to query actions by agent_id, following jvspatial
        entity-centric patterns.
        
        Args:
            enabled_only: If True, only return enabled actions
            
        Returns:
            List of action instances
        """
        try:
            # Build query filters - get agent_id from connected agent
            # For now, we need to traverse to get agent_id
            # Alternative: store agent_id on Actions node
            connected_nodes = await self.nodes(direction="both")
            agent = None
            for node in connected_nodes:
                # Import here to avoid circular dependency
                from jvagent.core.agent import Agent
                if isinstance(node, Agent):
                    agent = node
                    break
            
            if not agent:
                return []
            
            # Build entity-centric query
            filters = {"context.agent_id": agent.id}
            if enabled_only:
                filters["context.enabled"] = True
            
            # Use entity-centric find
            actions = await Action.find(filters)
            
            return actions
            
        except Exception as e:
            logger.error(f"Error getting actions: {e}", exc_info=True)
            return []
    
    async def get_action_by_label(self, label: str) -> Optional[Action]:
        """Get an action by its label using entity-centric queries.
        
        Args:
            label: Action label to search for
            
        Returns:
            Action instance if found, None otherwise
        """
        try:
            # Get agent_id
            connected_nodes = await self.nodes(direction="both")
            agent = None
            for node in connected_nodes:
                from jvagent.core.agent import Agent
                if isinstance(node, Agent):
                    agent = node
                    break
            
            if not agent:
                return None
            
            # Use entity-centric find
            actions = await Action.find({
                "context.agent_id": agent.id,
                "context.label": label
            })
            
            return actions[0] if actions else None
            
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


