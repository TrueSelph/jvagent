"""Agent loader for declarative agent installation from agent.yaml descriptors.

This module provides functionality to install and configure agents based on
their agent.yaml descriptors, including action setup and initialization.
"""

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.actions import Actions
from jvagent.action.base import Action
from jvagent.core.agent import Agent
from jvagent.core.agent_yaml_validator import warn_agent_yaml
from jvagent.core.app import App
from jvagent.core.yaml_io import load_yaml_sync
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
        from jvagent.action.loader import ActionLoader

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
            data = load_yaml_sync(agent_file)
            if not data:
                logger.warning(f"Empty agent descriptor: {agent_file}")
                return None

            from jvagent.core.env_resolver import resolve_env_placeholders

            data = resolve_env_placeholders(data)
            warn_agent_yaml(data, source=str(agent_file))

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

            # Converge duplicate action nodes (same namespace/label) left by
            # prior races, partial installs, or multi-worker boots. Runs on
            # EVERY install — not only under --update — so a plain restart
            # heals accumulated duplicates instead of carrying them forward.
            # AUDIT-core C1 / ADR-0033.
            await self._dedupe_actions_by_identity(agent, actions_manager)

            # Run _install_actions when: (a) agent has actions to install, or
            # (b) update_mode is set (to sync: remove actions no longer in descriptor)
            if descriptor.actions or update_mode is not None:
                self.action_loader.invalidate_core_cache()

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

    async def _dedupe_actions_by_identity(
        self, agent: Agent, actions_manager: Actions
    ) -> int:
        """Collapse duplicate action nodes sharing ``(namespace, label)``.

        Canonical action identity is ``(agent_id, namespace, label)``. On the
        default JSON adapter uniqueness is not enforced (``create_index`` is a
        no-op), and the ``find_one`` existence check in ``register_action`` is
        blind to persisted actions whose Python class is not imported at check
        time (jvspatial filters queries by imported subclass names). So a
        prior race, a partial install, or two workers booting concurrently can
        leave several nodes with the same identity, splitting the action across
        managers — some invisible to the walker.

        This pass runs on every install and keeps exactly one node per
        identity, preferring one connected to the Actions manager (so the
        walker keeps seeing it), then an enabled one, then the lexicographically
        smallest id for determinism. Duplicates are removed at the data level
        (disconnect + ``delete(cascade=True)``) — deliberately NOT via
        ``deregister_action``, whose module-unload/endpoint-teardown would
        disturb the surviving node that shares the same class and module.

        Returns the number of duplicate nodes removed.
        """
        from jvspatial.core.entities.node import Node

        try:
            all_records = await self._get_all_action_records(agent)
        except Exception as e:
            logger.warning(
                "dedupe: could not read action records for %s: %s", agent.name, e
            )
            return 0

        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for record in all_records:
            ctx = record.get("context", {})
            key = (ctx.get("namespace"), ctx.get("label"))
            groups.setdefault(key, []).append(record)

        removed = 0
        removed_labels: List[str] = []

        for (ns, label), records in groups.items():
            if len(records) < 2:
                continue

            keeper_id = await self._choose_keeper_id(actions_manager, records)

            for record in records:
                rid = record.get("id")
                if rid == keeper_id or not rid:
                    continue
                try:
                    node = await Node.get(rid)
                    if node is None:
                        continue
                    if await actions_manager.is_connected_to(node):
                        await actions_manager.disconnect(node)
                    await node.delete(cascade=True)
                    removed += 1
                    removed_labels.append(f"{ns}/{label}")
                except Exception as e:
                    logger.warning(
                        "dedupe: failed to remove duplicate action %s/%s (%s): %s",
                        ns,
                        label,
                        rid,
                        e,
                    )

            # Ensure the surviving node is connected to the manager (a removed
            # duplicate may have been the connected one).
            if keeper_id:
                try:
                    keeper = await Node.get(keeper_id)
                    if (
                        keeper is not None
                        and not await actions_manager.is_connected_to(keeper)
                    ):
                        await actions_manager.connect(keeper, direction="both")
                except Exception as e:
                    logger.warning(
                        "dedupe: failed to reconnect kept action %s/%s: %s",
                        ns,
                        label,
                        e,
                    )

        if removed:
            logger.warning(
                "Removed %d duplicate action node(s) for %s: %s",
                removed,
                agent.name,
                ", ".join(sorted(set(removed_labels))),
            )
            # Re-sync counts from ground truth after structural changes.
            try:
                await actions_manager.update_statistics()
            except Exception as e:
                logger.warning("dedupe: recount failed for %s: %s", agent.name, e)

        return removed

    async def _choose_keeper_id(
        self, actions_manager: Actions, records: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Pick which duplicate to keep: connected first, then enabled, then id.

        Prefers a node still connected to the Actions manager (the walker sees
        those), then an enabled node, with a deterministic lexicographic id
        tie-break so concurrent processes converge on the same survivor.
        """
        from jvspatial.core.entities.node import Node

        scored: List[Tuple[int, int, str]] = []
        for record in records:
            rid = record.get("id")
            if not rid:
                continue
            connected = 0
            enabled = 0
            try:
                node = await Node.get(rid)
                if node is not None:
                    if await actions_manager.is_connected_to(node):
                        connected = 1
                    enabled = 1 if getattr(node, "enabled", False) else 0
            except Exception:
                pass
            # Lower sort tuple wins: prefer connected, then enabled, then
            # smallest id. Negate the "good" flags so they sort first.
            scored.append((-connected, -enabled, rid))

        if not scored:
            return None
        scored.sort()
        return scored[0][2]

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
        """Return all node records for this agent from the raw DB (no entity filter)."""
        from jvagent.core.jvspatial_compat import find_raw_node_records

        raw = await find_raw_node_records("agent_id", agent.id)
        return [
            r
            for r in raw
            if r.get("context", {}).get("namespace")
            and r.get("context", {}).get("label")
        ]

    @staticmethod
    def _is_ghost_record(
        record: Dict[str, Any],
        *,
        expected_import_paths: Optional[Set[str]] = None,
        expected_actions: Optional[Set[Tuple[str, str]]] = None,
    ) -> bool:
        """Check if a DB record's Python module is importable.

        Uses ``importlib.util.find_spec`` to directly test whether the module
        referenced by the record can be found. This replaces the indirect
        approach of relying on ``Action.get()`` returning None.

        When *expected_import_paths* and *expected_actions* are provided, a row
        whose ``(namespace, label)`` is still declared in agent.yaml but whose
        persisted ``module_path`` is not among the filesystem-derived expected
        paths is treated as stale (layout/code moved) without calling
        ``find_spec``.

        For core actions, the ``core_module_path`` metadata field is the
        fully-qualified module path. For non-core actions, the importable path
        is derived from ``module_root`` and ``module``.

        Args:
            record: Raw DB record with context.metadata fields.

        Returns:
            True if the module is NOT importable (ghost), False otherwise.
        """
        from importlib.util import find_spec

        ctx = record.get("context") or {}
        ns = ctx.get("namespace")
        lbl = ctx.get("label")
        key = (ns, lbl) if ns and lbl else None
        mp = (ctx.get("module_path") or "").strip()

        if (
            expected_import_paths is not None
            and expected_actions is not None
            and key is not None
            and key in expected_actions
            and mp
            and mp not in expected_import_paths
        ):
            return True

        meta = ctx.get("metadata") or {}
        if not meta:
            return True

        if meta.get("is_core_action"):
            module_path = meta.get("core_module_path")
            if not module_path:
                return True
            return find_spec(module_path) is None

        module_root = meta.get("module_root")
        module = meta.get("module")
        if not module_root or not module:
            return True

        # Ensure the module root is on sys.path so find_spec can locate it
        module_path = module
        if os.path.isdir(module_root) and module_root not in sys.path:
            try:
                sys.path.insert(0, module_root)
                result = find_spec(module_path) is None
                return result
            finally:
                sys.path.remove(module_root)

        return find_spec(module_path) is None

    async def _reconcile_actions(
        self,
        agent: Agent,
        actions_manager: Actions,
        expected_actions: set[Tuple[str, str]],
        all_records: List[Dict[str, Any]],
        *,
        expected_import_paths: Optional[Set[str]] = None,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Remove stale action nodes and deduplicate, using raw DB records as truth.

        Ghost detection is explicit via :meth:`_is_ghost_record` which tests
        module importability directly, rather than relying on ``Action.get()``
        returning None as a proxy signal.

        For live actions, ``deregister_action()`` is used so lifecycle hooks,
        endpoint unregistration and module unloading all run correctly.

        For ghost actions (module not importable), the node is fetched via
        ``Node.get()`` and deleted with ``cascade=True``.

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
                if self._is_ghost_record(
                    record,
                    expected_import_paths=expected_import_paths,
                    expected_actions=expected_actions,
                ):
                    # Ghost: module not importable — delete directly via Node
                    node = await Node.get(record_id)
                    if node:
                        await node.delete(cascade=True)
                        removed += 1
                        removed_logged.append(f"{ns}/{label} (ghost)")
                    else:
                        logger.warning(
                            f"Could not retrieve ghost node {ns}/{label} ({record_id}); skipping"
                        )
                else:
                    # Live: module is importable — use full deregister flow
                    await actions_manager.deregister_action(record_id)
                    removed += 1
                    removed_logged.append(f"{ns}/{label}")

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
            expected_paths = (
                self.action_loader.expected_import_paths_from_descriptor_actions(
                    descriptor.actions
                )
            )
            all_records = await self._get_all_action_records(agent)
            kept_map = await self._reconcile_actions(
                agent,
                actions_manager,
                expected_actions,
                all_records,
                expected_import_paths=expected_paths,
            )

            # Reload modules for surviving actions so code changes take effect.
            # Use raw DB records for metadata — avoid Action.get() here so we do not
            # deserialize before action classes are loaded (subclass cache poisoning).
            #
            # Unified strategy: collect all loaded module paths across kept actions,
            # deduplicate, and reload each once via importlib.reload. This replaces
            # the previous core/non-core split which used different reload mechanisms.
            modules_to_reload: Dict[str, str] = {}  # module_name -> action label
            for (ns, label), record in kept_map.items():
                try:
                    metadata_dict = (record.get("context") or {}).get("metadata") or {}
                    loaded_modules = metadata_dict.get("loaded_modules") or []
                    for mod_name in loaded_modules:
                        if (
                            mod_name
                            and mod_name in sys.modules
                            and mod_name not in modules_to_reload
                        ):
                            modules_to_reload[mod_name] = f"{ns}/{label}"
                except Exception:
                    pass

            for mod_name, action_label in modules_to_reload.items():
                mod = sys.modules[mod_name]
                if getattr(mod, "__spec__", None) is None:
                    # Synthetic namespace package (e.g. ``jvagent.actions``) created by
                    # ``ensure_action_parent_packages``. No spec, no source — nothing to
                    # reload. The leaf action module carries the code that changes.
                    continue
                try:
                    importlib.reload(mod)
                    logger.debug("Reloaded module %s (from %s)", mod_name, action_label)
                except Exception as e:
                    logger.warning(
                        "Error reloading module %s for action %s: %s",
                        mod_name,
                        action_label,
                        e,
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
            agent_id_for_cache = getattr(agent, "id", None)

            app = await App.get()
            agents_manager = await app.node(node="Agents") if app else None

            await agent.delete()
            logger.info(f"Uninstalled agent: {namespace}/{agent_name}")

            # AUDIT-core M-4: drop every agent-scoped cache so subsequent
            # lookups don't return the now-deleted agent for up to TTL.
            if agent_id_for_cache:
                try:
                    from jvagent.core.cache import (
                        invalidate_action_cache,
                        invalidate_action_type_index,
                        invalidate_agent_cache,
                    )

                    await invalidate_agent_cache(agent_id_for_cache)
                    await invalidate_action_cache(agent_id_for_cache)
                    await invalidate_action_type_index(agent_id_for_cache)
                except Exception as cache_exc:
                    logger.warning(
                        "uninstall_agent: cache invalidation failed for %s: %s",
                        agent_id_for_cache,
                        cache_exc,
                    )

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
