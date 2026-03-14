"""Agent loader for declarative agent installation from agent.yaml descriptors.

This module provides functionality to install and configure agents based on
their agent.yaml descriptors, including action setup and initialization.
"""

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from jvagent.action.actions import Actions
from jvagent.action.base import Action
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
            parsed_namespace, parsed_name = agent_ref.split("/", 1)
            self.namespace = parsed_namespace
            self.name = parsed_name
        else:
            self.namespace = namespace
            self.name = data.get("name", "")

        # Top-level metadata
        self.version = data.get("version", "0.0.1")
        self.author = data.get("author", "")
        self.jvagent_version = data.get("jvagent", "")

        context = data.get("context", {})
        if not isinstance(context, dict):
            context = {}
        self._explicit_context_keys = set(context.keys())

        self.alias = context.get(
            "alias", self.name.replace("_", " ").title() if self.name else ""
        )
        self.enabled = context.get("enabled", True)
        self.description = context.get("description", "")

        # Additional properties from context (excluding reserved fields)
        self.properties = {
            k: v
            for k, v in context.items()
            if k not in ["alias", "enabled", "description"]
        }

        # Actions list
        self.actions = data.get("actions", [])

    def __repr__(self) -> str:
        return f"AgentDescriptor(namespace={self.namespace}, name={self.name}, version={self.version})"


class AgentLoader:
    """Loader for installing agents from agent.yaml descriptors."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize the agent loader.

        Args:
            base_path: Base path to search for agents. If None, uses current directory.
        """
        from jvagent.action.action_loader import ActionLoader

        self.base_path = Path(base_path or os.getcwd())
        self.action_loader = ActionLoader(str(self.base_path))

    def load_agent_descriptor(
        self, namespace: str, agent_name: str
    ) -> Optional[AgentDescriptor]:
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

            from jvagent.core.env_resolver import resolve_env_placeholders

            data = resolve_env_placeholders(data)

            return AgentDescriptor(data, agent_path, namespace=namespace)

        except Exception as e:
            logger.error(
                f"Error loading agent descriptor from {agent_file}: {e}", exc_info=True
            )
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

        for namespace_dir in agents_path.iterdir():
            if not namespace_dir.is_dir():
                continue

            namespace = namespace_dir.name

            for agent_dir in namespace_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                agent_file = agent_dir / "agent.yaml"
                if agent_file.exists():
                    discovered.append((namespace, agent_dir.name))

        return discovered

    async def install_agent(
        self, namespace: str, agent_name: str, update_mode: Optional[str] = None
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
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip

        Returns:
            Agent instance if successful, None otherwise
        """
        descriptor = self.load_agent_descriptor(namespace, agent_name)
        if not descriptor:
            return None

        try:
            existing_agent = await Agent.find_one(
                {
                    "context.name": descriptor.name,
                    "context.namespace": descriptor.namespace,
                }
            )

            if existing_agent:
                if update_mode is None:
                    return existing_agent

                agent = existing_agent
                if update_mode == "source":
                    agent.enabled = descriptor.enabled
                    agent.description = descriptor.description
                    agent.alias = descriptor.alias
                    _apply_properties(
                        agent,
                        descriptor.properties,
                        reserved={"id", "name", "namespace"},
                    )
                # merge mode: preserve all DB values, no property overwrites
                await agent.save()
            else:
                agent_data = {
                    "name": descriptor.name,
                    "namespace": descriptor.namespace,
                    "alias": descriptor.alias,
                    "enabled": descriptor.enabled,
                    "description": descriptor.description,
                }

                if descriptor.properties:
                    for key, value in descriptor.properties.items():
                        if not key.startswith("_") and key not in {
                            "id",
                            "namespace",
                            "name",
                        }:
                            agent_data[key] = value

                agent = await Agent.create(**agent_data)

                # Connect to Agents manager
                app = await App.get()
                if app:
                    agents_manager = await app.node(node="Agents")
                    if agents_manager:
                        await agents_manager.connect(agent, direction="both")
                        agents_manager.total_agents += 1
                        if descriptor.enabled:
                            agents_manager.active_agents += 1
                        await agents_manager.save()

            # Ensure Actions and Memory nodes exist for this agent
            actions_manager = await self._ensure_actions_node(agent)
            await self._ensure_memory_node(agent)

            # Run _install_actions when: (a) agent has actions to install, or
            # (b) update_mode is set (to sync: remove actions no longer in descriptor)
            if descriptor.actions or update_mode is not None:
                self.action_loader._core_action_path = None
                self.action_loader._core_action_cache = None

                await self._install_actions(
                    agent,
                    descriptor,
                    actions_manager,
                    update_mode=update_mode,
                )

            return agent

        except Exception as e:
            logger.error(f"Error installing agent {agent_name}: {e}", exc_info=True)
            return None

    async def _ensure_actions_node(self, agent: Agent) -> Actions:
        """Ensure Actions node exists for agent."""
        from jvagent.action.actions import Actions

        actions = await agent.node(node="Actions")
        if actions:
            return actions

        actions = await Actions.create()
        await agent.connect(actions, direction="both")
        return actions

    async def _ensure_memory_node(self, agent: Agent) -> Memory:
        """Ensure Memory node exists for agent."""
        memory = await agent.node(node="Memory")
        if memory:
            return memory

        memory = await Memory.create()
        await agent.connect(memory, direction="both")
        return memory

    # ============================================================================
    # Action installation
    # ============================================================================

    def _get_expected_actions_from_descriptor(
        self, descriptor: AgentDescriptor
    ) -> set[Tuple[str, str]]:
        """Extract (namespace, label) pairs from agent.yaml descriptor."""
        expected: set[Tuple[str, str]] = set()
        for action_config in descriptor.actions or []:
            if not isinstance(action_config, dict):
                continue
            action_ref = action_config.get("action", "")
            if action_ref and "/" in action_ref:
                namespace, label = action_ref.split("/", 1)
                expected.add((namespace, label))
        return expected

    async def _get_all_action_records(self, agent: Agent) -> List[Dict[str, Any]]:
        """Return all node records for this agent from the raw DB (no entity filter).

        Bypasses jvspatial's entity-type filtering so ghost nodes whose classes
        are not imported (removed actions) are visible.
        """
        from jvspatial.core.context import get_default_context
        from jvspatial.core.entities.node import Node

        context = get_default_context()
        type_code = context._get_entity_type_code(Node)
        collection = context._get_collection_name(type_code)
        raw = await context.database.find(collection, {"context.agent_id": agent.id})
        return [
            r
            for r in raw
            if r.get("context", {}).get("namespace")
            and r.get("context", {}).get("label")
        ]

    async def _reconcile_actions(
        self,
        agent: Agent,
        actions_manager: Actions,
        expected_actions: set[Tuple[str, str]],
        all_records: List[Dict[str, Any]],
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Remove stale action nodes and deduplicate, using raw DB records as truth.

        Handles both live actions (whose modules are imported) and ghost nodes
        (whose entity type is absent from _collect_class_names() because their
        modules are no longer pre-imported).

        For live actions, deregister_action() is used so lifecycle hooks,
        endpoint unregistration and module unloading all run correctly.

        For ghost actions, Action.get() returns None (entity class not imported),
        but Node.get() now falls back to the Node base class for unknown entity
        types (jvspatial context.get() ghost-node fallback).  node.delete(cascade=True)
        then cleans up all edges and the node record through the standard interface.

        Args:
            agent: Agent instance
            actions_manager: Actions manager node
            expected_actions: Set of (namespace, label) tuples from agent.yaml
            all_records: Raw DB records from _get_all_action_records()

        Returns:
            Map of (namespace, label) -> raw record for the surviving (kept) actions
        """
        from jvspatial.core.entities.node import Node

        # Partition into to_keep (expected, first per ns/label) and to_remove (everything else).
        # Any duplicate within expected_actions is also sent to to_remove.
        kept_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        to_remove: List[Dict[str, Any]] = []

        for record in all_records:
            ctx = record.get("context", {})
            ns = ctx["namespace"]
            label = ctx["label"]
            key = (ns, label)

            if key in expected_actions and key not in kept_map:
                kept_map[key] = record
            else:
                to_remove.append(record)

        # Remove stale / duplicate records
        removed = 0
        removed_logged: List[str] = []

        for record in to_remove:
            record_id = record.get("id")
            ctx = record.get("context", {})
            ns = ctx.get("namespace", "?")
            label = ctx.get("label", "?")

            try:
                # Live path: action class is imported — use full deregister flow
                # (triggers lifecycle hooks, endpoint unregister, module unload)
                action = await Action.get(record_id)
                if action:
                    await actions_manager.deregister_action(record_id)
                    removed += 1
                    removed_logged.append(f"{ns}/{label}")
                    continue

                # Ghost path: class not imported, Action.get() returned None.
                # Node.get() falls back to the Node base class for unknown entity
                # types, so we get a proper instance and call delete(cascade=True)
                # through the standard jvspatial interface — no raw DB calls.
                node = await Node.get(record_id)
                if node:
                    await node.delete(cascade=True)
                    removed += 1
                    removed_logged.append(f"{ns}/{label} (ghost)")
                else:
                    logger.warning(
                        f"Could not retrieve node {ns}/{label} ({record_id}) "
                        "for removal; skipping"
                    )

            except Exception as e:
                logger.warning(
                    f"Error removing action node {ns}/{label} ({record_id}): {e}"
                )

        if removed_logged:
            logger.info(
                f"Removed {removed} action(s) for {agent.name}: "
                + ", ".join(removed_logged)
            )

        # Recount from ground truth (kept_map). deregister_action adjusts counts
        # for live actions; set final values here to correct for any drift.
        try:
            kept_nodes: List[Action] = []
            for record in kept_map.values():
                node = await Action.get(record["id"])
                if node:
                    kept_nodes.append(node)

            actions_manager.registered_count = len(kept_nodes)
            actions_manager.enabled_count = sum(1 for n in kept_nodes if n.enabled)
            await actions_manager.save()
        except Exception as e:
            logger.warning(f"Could not recount action statistics for {agent.name}: {e}")

        return kept_map

    async def _install_actions(
        self,
        agent: Agent,
        descriptor: AgentDescriptor,
        actions_manager: Actions,
        update_mode: Optional[str] = None,
    ) -> None:
        """Install or update actions for an agent.

        When update_mode is set, agent.yaml is the source of truth:
        - Actions not in agent.yaml are removed (including ghosts with unimported classes)
        - Existing actions have their modules reloaded
        - New actions in agent.yaml are registered
        - "merge" preserves DB state; "source" does a full overwrite

        Args:
            agent: Agent instance
            descriptor: Agent descriptor with action configurations
            actions_manager: Actions manager node
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip
        """
        expected_actions = self._get_expected_actions_from_descriptor(descriptor)

        kept_map: Dict[Tuple[str, str], Dict[str, Any]] = {}

        if update_mode is not None:
            all_records = await self._get_all_action_records(agent)
            kept_map = await self._reconcile_actions(
                agent, actions_manager, expected_actions, all_records
            )

            # Reload modules for surviving actions so code changes take effect
            for (ns, label), record in kept_map.items():
                try:
                    node = await Action.get(record["id"])
                    if not node:
                        continue
                    metadata_dict = node.metadata
                    is_core = metadata_dict.get("is_core_action", False)
                    core_module_path = metadata_dict.get("core_module_path")

                    if is_core and core_module_path:
                        if core_module_path in sys.modules:
                            importlib.reload(sys.modules[core_module_path])
                    else:
                        await node._unload_action_modules()
                except Exception as e:
                    logger.warning(
                        f"Error reloading modules for action {ns}/{label}: {e}",
                        exc_info=True,
                    )

        # Load actions from filesystem and register / update
        actions = self.action_loader.load_actions_for_agent(
            descriptor.namespace, descriptor.name, agent.id, descriptor.actions
        )

        if not actions:
            return

        actions_to_register = [
            a for a in actions if (a.namespace, a.label) in expected_actions
        ]
        results = await actions_manager.register_actions(
            actions_to_register, update_mode=update_mode
        )

        registered_count = sum(
            1 for ok in results.values() if ok and update_mode is None
        )
        updated_count = sum(
            1 for ok in results.values() if ok and update_mode is not None
        )
        failed_count = sum(1 for ok in results.values() if not ok)
        removed_count = (
            max(0, len(kept_map) - sum(results.values()))
            if update_mode is not None
            else 0
        )

        for label, success in results.items():
            if not success:
                logger.warning(f"Failed to register action: {label}")

        if update_mode is not None:
            parts = []
            if len(kept_map) > updated_count:
                parts.append(f"{len(kept_map) - updated_count} removed")
            if registered_count:
                parts.append(f"{registered_count} registered")
            if updated_count:
                parts.append(f"{updated_count} updated")
            if failed_count:
                parts.append(f"{failed_count} failed")
            if parts:
                logger.info(f"Actions for {agent.name}: {', '.join(parts)}")
        elif failed_count:
            logger.warning(
                f"Actions for {agent.name}: {registered_count} registered, {failed_count} failed"
            )

    async def install_all_agents(
        self, update_mode: Optional[str] = None
    ) -> List[Agent]:
        """Install all discovered agents.

        Args:
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip

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
            agent = await self.install_agent(
                namespace, agent_name, update_mode=update_mode
            )
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
            agent = await Agent.find_one(
                {"context.name": agent_name, "context.namespace": namespace}
            )

            if not agent:
                logger.warning(f"Agent '{namespace}/{agent_name}' not found")
                return False
            was_enabled = agent.enabled

            app = await App.get()
            agents_manager = await app.node(node="Agents") if app else None

            await agent.delete()
            logger.info(f"Uninstalled agent: {namespace}/{agent_name}")

            if agents_manager:
                agents_manager.total_agents = max(0, agents_manager.total_agents - 1)
                if was_enabled:
                    agents_manager.active_agents = max(
                        0, agents_manager.active_agents - 1
                    )
                await agents_manager.save()

            return True

        except Exception as e:
            logger.error(f"Error uninstalling agent {agent_name}: {e}", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------


def _apply_properties(
    target: Any,
    properties: Optional[Dict[str, Any]],
    reserved: Optional[set] = None,
) -> None:
    """Apply property overrides from a descriptor to a node instance.

    Skips private keys (leading ``_``) and any explicitly reserved keys.
    Only sets attributes that already exist on the target.

    Args:
        target: Node instance to update
        properties: Dictionary of property key/value pairs from YAML context
        reserved: Additional keys to skip beyond private ones (e.g. ``{"id", "name"}``)
    """
    if not properties:
        return

    _reserved = reserved or set()

    for key, value in properties.items():
        if key.startswith("_") or key in _reserved:
            continue
        if not hasattr(target, key):
            continue
        try:
            setattr(target, key, value)
        except Exception as e:
            logger.warning(f"Could not set {type(target).__name__}.{key}: {e}")
