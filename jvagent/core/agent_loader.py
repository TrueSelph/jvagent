"""Agent loader for declarative agent installation from agent.yaml descriptors.

This module provides functionality to install and configure agents based on
their agent.yaml descriptors, including action setup and initialization.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from jvagent.action.base import Action
from jvagent.action.action_loader import ActionLoader
from jvagent.action.actions import Actions
from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.memory import Memory

logger = logging.getLogger(__name__)


class AgentDescriptor:
    """Container for agent metadata loaded from agent.yaml.

    Format:
    - agent: namespace/agent_name (extracts namespace and name)
    - version: at top level
    - author: at top level
    - context: object containing agent properties (alias, description, enabled, etc.)
    - actions: list with action: namespace/action_name and context: for each
    """

    def __init__(self, data: Dict[str, Any], path: Path, namespace: str = ""):
        """Initialize agent descriptor.

        Args:
            data: Parsed YAML data from agent.yaml
            path: Path to the agent directory
            namespace: Namespace for the agent (from folder structure)
        """
        self.data = data
        self.path = path

        # Extract agent reference: agent: namespace/agent_name
        agent_ref = data.get("agent", "")
        if agent_ref and "/" in agent_ref:
            # Parse namespace/agent_name from agent field
            parsed_namespace, parsed_name = agent_ref.split("/", 1)
            self.namespace = parsed_namespace
            self.name = parsed_name
        else:
            # Use folder structure namespace and name field
            self.namespace = namespace
            self.name = data.get("name", "")

        # Top-level metadata
        self.version = data.get("version", "0.0.1")
        self.author = data.get("author", "")
        self.jvagent_version = data.get("jvagent", "")

        # Extract properties from context object
        context = data.get("context", {})
        if not isinstance(context, dict):
            context = {}

        self.alias = context.get("alias", self.name.replace("_", " ").title() if self.name else "")
        self.enabled = context.get("enabled", True)
        self.description = context.get("description", "")

        # Additional properties from context (excluding reserved fields)
        self.properties = {
            k: v for k, v in context.items() if k not in ["alias", "enabled", "description"]
        }

        # Actions list
        self.actions = data.get("actions", [])

    def __repr__(self) -> str:
        return (
            f"AgentDescriptor(namespace={self.namespace}, name={self.name}, version={self.version})"
        )


class AgentLoader:
    """Loader for installing agents from agent.yaml descriptors."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize the agent loader.

        Args:
            base_path: Base path to search for agents. If None, uses current directory.
        """
        self.base_path = Path(base_path or os.getcwd())
        self.action_loader = ActionLoader(str(self.base_path))

    def load_agent_descriptor(self, namespace: str, agent_name: str) -> Optional[AgentDescriptor]:
        """Load agent descriptor from agent.yaml.

        Args:
            namespace: Namespace of the agent
            agent_name: Name of the agent (directory name)

        Returns:
            AgentDescriptor if found and valid, None otherwise
        """
        agent_path = self.base_path / "agents" / namespace / agent_name
        agent_file = agent_path / "agent.yaml"

        if not agent_file.exists():
            logger.warning(f"Agent descriptor not found: {agent_file}")
            return None

        try:
            with open(agent_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                logger.warning(f"Empty agent descriptor: {agent_file}")
                return None

            # Resolve environment variable placeholders
            from jvagent.core.env_resolver import resolve_env_placeholders

            data = resolve_env_placeholders(data)

            return AgentDescriptor(data, agent_path, namespace=namespace)

        except Exception as e:
            logger.error(f"Error loading agent descriptor from {agent_file}: {e}", exc_info=True)
            return None

    def discover_agents(self) -> List[tuple[str, str]]:
        """Discover all agents in the agents directory.

        Scans the agents directory for namespace subdirectories,
        then within each namespace for agent directories containing agent.yaml files.

        Directory structure:
        agents/{namespace}/{agent_name}/agent.yaml

        Returns:
            List of (namespace, agent_name) tuples
        """
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            return []

        discovered = []

        # Iterate through namespace directories in the agents folder
        for namespace_dir in agents_path.iterdir():
            if not namespace_dir.is_dir():
                continue

            namespace = namespace_dir.name

            # Iterate through agent directories within each namespace
            for agent_dir in namespace_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                # Look for agent.yaml file
                agent_file = agent_dir / "agent.yaml"
                if agent_file.exists():
                    discovered.append((namespace, agent_dir.name))

        return discovered

    async def install_agent(
        self, namespace: str, agent_name: str, update_if_exists: bool = False
    ) -> Optional[Agent]:
        """Install an agent from its descriptor.

        This method:
        1. Loads the agent descriptor from agent.yaml
        2. Creates or updates the Agent node
        3. Applies property overrides from agent.yaml
        4. Connects it to the Agents manager
        5. Creates Actions and Memory nodes
        6. Loads and registers actions

        Args:
            namespace: Namespace of the agent
            agent_name: Name of the agent to install
            update_if_exists: If True, update existing agent; if False, skip if exists

        Returns:
            Agent instance if successful, None otherwise
        """
        # Load agent descriptor
        descriptor = self.load_agent_descriptor(namespace, agent_name)
        if not descriptor:
            return None

        try:
            # Check if agent already exists (by namespace and name)
            existing_agent = await Agent.find_one(
                {"context.name": descriptor.name, "context.namespace": descriptor.namespace}
            )

            if existing_agent:
                if not update_if_exists:
                    # Agent already exists - skip silently (summary will show installed count)
                    return existing_agent

                # Update existing agent
                agent = existing_agent
                agent.enabled = descriptor.enabled
                agent.description = descriptor.description
                agent.alias = descriptor.alias

                # Apply property overrides from agent.yaml
                self._apply_agent_properties(agent, descriptor)

                await agent.save()
                logger.debug(f"Updated existing agent: {descriptor.namespace}/{descriptor.name}")
            else:
                # Build initial agent data
                agent_data = {
                    "name": descriptor.name,
                    "namespace": descriptor.namespace,
                    "alias": descriptor.alias,
                    "enabled": descriptor.enabled,
                    "description": descriptor.description,
                }

                # Apply property overrides from agent.yaml
                if descriptor.properties:
                    for key, value in descriptor.properties.items():
                        # Only override if it's a valid field (don't override private fields)
                        if not key.startswith("_") and key not in ["id", "namespace", "name"]:
                            agent_data[key] = value

                # Create new agent with merged properties
                agent = await Agent.create(**agent_data)
                logger.debug(f"Created agent: {descriptor.namespace}/{descriptor.name}")

                # Connect to Agents manager
                app = await App.get()
                if app:
                    agents_manager = await app.node(node="Agents")

                    if agents_manager:
                        await agents_manager.connect(agent, direction="both")

                        # Update statistics
                        agents_manager.total_agents += 1
                        if descriptor.enabled:
                            agents_manager.active_agents += 1
                        await agents_manager.save()

            # Ensure Actions node exists for this agent
            actions_manager = await self._ensure_actions_node(agent)

            # Ensure Memory node exists for this agent
            await self._ensure_memory_node(agent)

            # Load and register/update actions from agent.yaml
            # Always use update_if_exists when update is requested at app level
            # This ensures any remaining actions (if removal didn't catch them) are properly handled
            if descriptor.actions:
                await self._install_actions(
                    agent, descriptor, actions_manager, update_if_exists=update_if_exists
                )

            return agent

        except Exception as e:
            logger.error(f"Error installing agent {agent_name}: {e}", exc_info=True)
            return None

    def _apply_agent_properties(self, agent: Agent, descriptor: AgentDescriptor) -> None:
        """Apply property overrides from descriptor to agent instance.

        Args:
            agent: Agent instance to update
            descriptor: Agent descriptor with property overrides
        """
        if not descriptor.properties:
            return

        for key, value in descriptor.properties.items():
            # Only set public properties (not private, not id, not name - name is static)
            if not key.startswith("_") and key not in ["id", "name"] and hasattr(agent, key):
                try:
                    setattr(agent, key, value)
                    logger.debug(f"Set agent.{key} = {value}")
                except Exception as e:
                    logger.warning(f"Could not set agent.{key}: {e}")

    async def _ensure_actions_node(self, agent: Agent):
        """Ensure Actions node exists for agent.

        Args:
            agent: Agent instance

        Returns:
            Actions node instance
        """
        # Import here to avoid circular dependency
        from jvagent.action.actions import Actions

        # Check if Actions node already connected
        actions = await agent.node(node="Actions")
        if actions:
            return actions

        # Create new Actions node
        actions = await Actions.create()
        await agent.connect(actions, direction="both")
        logger.debug(f"Created Actions node for agent {agent.name}")

        return actions

    async def _ensure_memory_node(self, agent: Agent) -> Memory:
        """Ensure Memory node exists for agent.

        Args:
            agent: Agent instance

        Returns:
            Memory node instance
        """
        # Check if Memory node already connected
        memory = await agent.node(node="Memory")
        if memory:
            return memory

        # Create new Memory node
        memory = await Memory.create()
        await agent.connect(memory, direction="both")
        logger.debug(f"Created Memory node for agent {agent.name}")

        return memory

    async def _install_actions(
        self,
        agent: Agent,
        descriptor: AgentDescriptor,
        actions_manager,
        update_if_exists: bool = False,
    ) -> None:
        """Install or update actions for an agent.

        Actions are loaded from:
        1. Discovered from filesystem: agents/{namespace}/{agent_name}/actions/{namespace}/{action_name}/info.yaml
        2. Configured in agent.yaml: actions list with context overrides

        Existing actions will be updated with new context values if update_if_exists=True.

        Args:
            agent: Agent instance
            descriptor: Agent descriptor with action configurations
            actions_manager: Actions manager node
            update_if_exists: If True, update existing actions; if False, skip if exists
        """
        # Load actions using ActionLoader
        # This discovers actions from filesystem and applies configuration from agent.yaml
        actions = self.action_loader.load_actions_for_agent(
            descriptor.namespace, descriptor.name, agent.id, descriptor.actions
        )

        if not actions:
            logger.debug(f"No actions found for agent {agent.name}")
            return

        # Always deduplicate existing action nodes before registering new ones.
        await self._dedupe_agent_actions(agent, actions_manager)

        # Register or update actions with the manager
        results = await actions_manager.register_actions(actions, update_if_exists=update_if_exists)

        # Report results
        registered_count = 0
        updated_count = 0
        failed_count = 0

        # Check which actions were updates vs new registrations
        for action in actions:
            action_label = action.label
            success = results.get(action_label, False)

            if success:
                # Check if this was an update by looking for existing action
                existing_actions = await Action.find(
                    {
                        "context.agent_id": action.agent_id,
                        "context.namespace": action.namespace,
                        "context.label": action.label,
                    }
                )

                if existing_actions and update_if_exists:
                    updated_count += 1
                    logger.debug(f"    ✓ Action: {action.namespace}/{action_label} (updated)")
                else:
                    registered_count += 1
                    logger.debug(f"    ✓ Action: {action.namespace}/{action_label}")
            else:
                logger.warning(f"Failed to register action: {action_label}")
                failed_count += 1

        # Log summary with action count per agent
        total_actions = registered_count + updated_count
        if failed_count > 0:
            logger.warning(
                f"Actions for {agent.name}: {registered_count} registered, {updated_count} updated, {failed_count} failed"
            )
        elif total_actions > 0:
            logger.debug(
                f"Actions for {agent.name}: {total_actions} total ({registered_count} registered, {updated_count} updated)"
            )

    async def _dedupe_agent_actions(self, agent: Agent, actions_manager: Actions) -> None:
        """Ensure only one action node exists per (namespace, label) for the agent."""
        try:
            connected_nodes = await actions_manager.nodes(direction="out")
            action_nodes = [
                node
                for node in connected_nodes
                if isinstance(node, Action) and node.agent_id == agent.id
            ]
            total_connected = len(action_nodes)
            if total_connected == 0:
                return

            dedupe_map: Dict[Tuple[str, str], Action] = {}
            duplicates: List[Action] = []
            for action_node in action_nodes:
                key = (action_node.namespace, action_node.label)
                if key in dedupe_map:
                    duplicates.append(action_node)
                else:
                    dedupe_map[key] = action_node

            removed = 0
            for duplicate in duplicates:
                try:
                    if await actions_manager.is_connected_to(duplicate):
                        await actions_manager.disconnect(duplicate)
                    await duplicate.delete()
                    removed += 1
                except Exception as exc:
                    logger.error(
                        f"Error removing duplicate action {duplicate.id}: {exc}",
                        exc_info=True,
                    )

            connected_ids = {action.id for action in dedupe_map.values()}
            # Remove orphan actions that still reference the agent but are not connected
            orphan_candidates = await Action.find({"context.agent_id": agent.id})
            for orphan in orphan_candidates:
                if orphan.id in connected_ids:
                    continue
                try:
                    if await actions_manager.is_connected_to(orphan):
                        await actions_manager.disconnect(orphan)
                    await orphan.delete()
                    removed += 1
                except Exception as exc:
                    logger.error(
                        f"Error removing orphan action {orphan.id}: {exc}",
                        exc_info=True,
                    )

            actions_manager.registered_count = len(dedupe_map)
            actions_manager.enabled_count = sum(
                1 for action in dedupe_map.values() if action.enabled
            )

            if removed > 0:
                await actions_manager.save()
                logger.debug(f"Cleaned {removed} duplicate/orphan action(s) for agent {agent.name}")
        except Exception as exc:
            logger.error(
                f"Failed to deduplicate actions for agent {agent.id}: {exc}", exc_info=True
            )

    async def install_all_agents(self, update_if_exists: bool = False) -> List[Agent]:
        """Install all discovered agents.

        Args:
            update_if_exists: If True, update existing agents; if False, skip existing

        Returns:
            List of installed/updated agent instances
        """
        agent_list = self.discover_agents()

        if not agent_list:
            logger.info("No agents found to install")
            return []

        logger.info(f"Found {len(agent_list)} agent(s) to install")

        installed = []
        for namespace, agent_name in agent_list:
            agent = await self.install_agent(namespace, agent_name, update_if_exists)
            if agent:
                installed.append(agent)

        logger.info(f"Successfully installed {len(installed)} agent(s)")
        return installed

    async def uninstall_agent(self, namespace: str, agent_name: str) -> bool:
        """Uninstall an agent.

        This removes the agent and all its connected nodes (Actions, Memory, etc.)

        Args:
            namespace: Namespace of the agent
            agent_name: Name of the agent to uninstall

        Returns:
            True if successful, False otherwise
        """
        try:
            # Find the agent by namespace and name
            agent = await Agent.find_one(
                {"context.name": agent_name, "context.namespace": namespace}
            )

            if not agent:
                logger.warning(f"Agent '{namespace}/{agent_name}' not found")
                return False
            was_enabled = agent.enabled

            # Get Agents manager for statistics update
            # Need to get it before deleting the agent
            app = await App.get()
            agents_manager = await app.node(node="Agents") if app else None

            # Delete the agent (cascades to connected nodes)
            await agent.delete()
            logger.info(f"Uninstalled agent: {namespace}/{agent_name}")

            # Update Agents manager statistics
            if agents_manager:
                agents_manager.total_agents = max(0, agents_manager.total_agents - 1)
                if was_enabled:
                    agents_manager.active_agents = max(0, agents_manager.active_agents - 1)
                await agents_manager.save()

            return True

        except Exception as e:
            logger.error(f"Error uninstalling agent {agent_name}: {e}", exc_info=True)
            return False
