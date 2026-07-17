"""App loader for bootstrapping jvagent applications from app.yaml descriptors.

This module provides functionality to bootstrap the entire application graph
based on app.yaml configuration, including agents and their actions.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from jvspatial.core import Root
from jvspatial.env import env

from jvagent.core.agent import Agent  # noqa: F401
from jvagent.core.agent_loader import AgentLoader, _apply_properties
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.app_yaml_validator import warn_app_yaml_descriptor
from jvagent.core.bootstrap_logger import BootstrapLogger
from jvagent.core.config import get_file_storage_config, load_app_config

logger = logging.getLogger(__name__)


class AppDescriptor:
    """Container for application metadata loaded from app.yaml.

    Format:
    - app: app_name (application identifier)
    - version: at top level
    - author: at top level
    - context: object containing App node properties (name, description, etc.).
      File storage paths come from ``config.file_storage`` (or env overrides).
    - license: at top level (metadata, not stored in App node)
    - homepage: at top level (metadata, not stored in App node)
    - tags: at top level (metadata, not stored in App node)
    - config: at top level (application configuration)
    - agents: list of namespace/agent_name strings
    """

    def __init__(self, data: Dict[str, Any], path: Path):
        """Initialize app descriptor.

        Args:
            data: Parsed YAML data from app.yaml
            path: Path to the app directory
        """
        self.data = data
        self.path = path

        # Extract app identifier
        self.app_id = data.get("app", "jvagent_app")

        # Top-level metadata
        self.version = data.get("version", "0.0.1")
        self.author = data.get("author", "")
        self.jvagent_version = data.get("jvagent", "")

        context = data.get("context", {})
        if not isinstance(context, dict):
            context = {}
        self._explicit_context_keys = set(context.keys())

        self.name = context.get("name", "jvagent Application")
        self.description = context.get("description", "")
        # Same precedence as Server / get_file_storage_config: env > config.file_storage > defaults
        # (context.file_storage_* are legacy; config.file_storage in app.yaml is canonical)
        _app_config = load_app_config(str(path))
        _fs = get_file_storage_config(str(path), _app_config)
        self.file_storage_provider = _fs["provider"]
        self.file_storage_root_dir = _fs["root_dir"]
        self.file_storage_enabled = context.get("file_storage_enabled", True)
        self.logging_enabled = context.get("logging_enabled", True)
        retention_raw = env("JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS", default="")
        try:
            default_retention = int(retention_raw) if retention_raw else None
            if default_retention is not None and default_retention < 0:
                default_retention = None
        except ValueError:
            default_retention = None
        self.log_retention_days = (
            default_retention
            if default_retention is not None
            else context.get("log_retention_days", 60)
        )
        self.timezone = context.get("timezone")

        # Additional properties from context (excluding reserved fields)
        self.properties = {
            k: v
            for k, v in context.items()
            if k
            not in [
                "name",
                "description",
                "file_storage_provider",
                "file_storage_root_dir",
                "file_storage_enabled",
                "logging_enabled",
                "log_retention_days",
                "timezone",
                "update_mode",
            ]
        }

        # Metadata (not stored in App node)
        self.license = data.get("license", "")
        self.homepage = data.get("homepage", "")
        self.tags = data.get("tags", [])

        # App configuration
        self.config = data.get("config", {})

        # Agents to install (simple list of namespace/agent_name strings)
        agents_list = data.get("agents", [])
        self.agents = []
        for agent_ref in agents_list:
            if isinstance(agent_ref, str):
                # Simple string format: namespace/agent_name
                self.agents.append(agent_ref)
            else:
                logger.warning(
                    "Invalid agent reference format (expected string): %s", agent_ref
                )

    def __repr__(self) -> str:
        return (
            f"AppDescriptor(id={self.app_id}, name={self.name}, version={self.version})"
        )


class AppLoader:
    """Loader for bootstrapping applications from app.yaml descriptors."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize the app loader.

        Args:
            base_path: Base path to the app directory. If None, uses current directory.
        """
        self.base_path = Path(base_path or os.getcwd())
        self.agent_loader = AgentLoader(str(self.base_path))

    def load_app_descriptor(self) -> Optional[AppDescriptor]:
        """Load application descriptor from app.yaml.

        Returns:
            AppDescriptor if found and valid, None otherwise
        """
        app_file = self.base_path / "app.yaml"

        if not app_file.exists():
            logger.debug("App descriptor not found: %s", app_file)
            return None

        try:
            with open(app_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                logger.debug("Empty app descriptor: %s", app_file)
                return None

            # Resolve environment variable placeholders
            from jvagent.core.env_resolver import resolve_env_placeholders

            data = resolve_env_placeholders(data)
            warn_app_yaml_descriptor(data, source=str(app_file))

            return AppDescriptor(data, self.base_path)

        except Exception as e:
            logger.warning("Error loading app descriptor from %s: %s", app_file, e)
            return None

    async def bootstrap_application(
        self, update_mode: Optional[str] = None
    ) -> Optional[App]:
        """Bootstrap the application from app.yaml.

        This method:
        1. Ensures Root node exists
        2. Creates or updates App node based on app.yaml
        3. Creates Agents manager node
        4. Installs all agents specified in app.yaml

        Args:
            update_mode: Update strategy - "merge" for non-destructive merge, "source" for
                         destructive overwrite from YAML, or None to skip existing.

        Returns:
            App instance if successful, None otherwise
        """
        # Load app descriptor
        descriptor = self.load_app_descriptor()
        if not descriptor:
            logger.error("Failed to load app descriptor")
            return None

        bootstrap_log = BootstrapLogger(f"App: {descriptor.name}")
        bootstrap_log.start(f"v{descriptor.version}")

        try:
            # Step 0: Pre-import action modules CONDITIONALLY for agents listed in app.yaml
            # This implements conditional loading:
            # - Only actions explicitly listed in agent.yaml are loaded
            # - Action dependencies (from info.yaml) are transitively resolved and loaded
            # - Endpoints are only registered for loaded actions
            # - Unused actions remain completely unloaded (no module import, no endpoints)
            # This ensures Action subclasses are available for _collect_class_names()
            # before any queries are executed, while preventing unused endpoints from being accessible
            from jvagent.action.loader import ActionLoader

            action_loader = ActionLoader(base_path=str(self.base_path))
            # Only pre-import modules for agents listed in app.yaml (with dependency resolution)
            agent_refs = descriptor.agents if descriptor.agents else []
            action_loader.pre_import_action_modules_for_agents(agent_refs)

            # Step 1: Ensure Root node exists
            root = await Root.get()
            if root is None:
                logger.error("Failed to get Root node")
                return None

            # Step 2: Create or update App node
            app = await self._ensure_app_node(descriptor, update_mode, root)
            if not app:
                logger.error("Failed to create/update App node")
                return None

            # Step 3: Connect App to Root if not already connected
            if not await root.is_connected_to(app):
                await root.connect(app)

            # Step 4: Create or get Agents manager node
            agents_manager = await self._ensure_agents_node(app)
            if not agents_manager:
                logger.error("Failed to create/update Agents manager node")
                return None

            # Step 5: Install agents from app.yaml
            if descriptor.agents:
                await self._install_agents(descriptor, update_mode)
                await self._recount_agent_statistics(app)

            bootstrap_log.complete()
            return app

        except Exception as e:
            logger.error(f"Error bootstrapping application: {e}", exc_info=True)
            return None

    async def _ensure_app_node(
        self, descriptor: AppDescriptor, update_mode: Optional[str], root
    ) -> Optional[App]:
        """Ensure App node exists and is configured.

        Uses graph-based lookup (Root -> App) instead of name-based lookup so that
        when context.name changes in app.yaml, the existing App is updated in place
        rather than creating a duplicate.

        Args:
            descriptor: App descriptor
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip
            root: Root node for graph traversal

        Returns:
            App instance if successful, None otherwise
        """
        try:
            App.clear_cache()

            # Graph-based lookup: find App nodes connected to Root
            root_nodes = await root.nodes(direction="out")
            app_nodes = [n for n in root_nodes if isinstance(n, App)]

            # Deduplicate if multiple App nodes exist (corruption from prior buggy runs)
            if len(app_nodes) > 1:
                app_nodes = await self._deduplicate_app_nodes(
                    root, app_nodes, descriptor
                )
                if not app_nodes:
                    return None

            if app_nodes:
                app = app_nodes[0]
                if update_mode == "source":
                    await app.update(
                        {
                            "name": descriptor.name,
                            "version": descriptor.version,
                            "description": descriptor.description,
                            "file_storage_provider": descriptor.file_storage_provider,
                            "file_storage_root_dir": descriptor.file_storage_root_dir,
                            "file_storage_enabled": descriptor.file_storage_enabled,
                            "logging_enabled": descriptor.logging_enabled,
                            "log_retention_days": descriptor.log_retention_days,
                            "timezone": descriptor.timezone,
                            "app_id": descriptor.app_id,
                        },
                        skip_protected=True,
                    )
                    self._apply_app_properties(app, descriptor.properties)
                    await app.save()
                    logger.debug(f"Updated App node (source): {app.id}")
                elif update_mode == "merge":
                    await app.update(
                        {
                            "version": descriptor.version,
                            "app_id": descriptor.app_id,
                        },
                        skip_protected=True,
                    )
                    await app.save()
                    logger.debug(f"Updated App node (merge): {app.id}")

                App._set_cached_app(app)
                return app

            # No App found - create new
            app_data = {
                "name": descriptor.name,
                "version": descriptor.version,
                "description": descriptor.description,
                "file_storage_provider": descriptor.file_storage_provider,
                "file_storage_root_dir": descriptor.file_storage_root_dir,
                "file_storage_enabled": descriptor.file_storage_enabled,
                "logging_enabled": descriptor.logging_enabled,
                "log_retention_days": descriptor.log_retention_days,
                "timezone": descriptor.timezone,
            }
            app_data["app_id"] = descriptor.app_id

            if descriptor.properties:
                for key, value in descriptor.properties.items():
                    if not key.startswith("_") and key not in [
                        "id",
                        "name",
                        "update_mode",
                    ]:
                        app_data[key] = value

            app = await App.create(**app_data)
            logger.debug(f"Created App node: {app.id}")

            App._set_cached_app(app)
            return app
        except Exception as e:
            logger.error(f"Error in _ensure_app_node: {e}", exc_info=True)
            return None

    async def _deduplicate_app_nodes(
        self, root, app_nodes: list, descriptor: AppDescriptor
    ) -> list:
        """Keep canonical App, remove duplicates. Returns list with single App or empty."""
        if len(app_nodes) <= 1:
            return app_nodes

        async def _agents_count(app_node) -> tuple:
            """Return (total_agents, child_count) for canonical selection."""
            agents_count = 0
            child_count = 0
            for node in await app_node.nodes():
                child_count += 1
                if isinstance(node, Agents):
                    agents_count = node.total_agents
                    break
            return (agents_count, child_count)

        # Prefer App with Agents that has total_agents > 0; tie-break by child count
        scores = []
        for a in app_nodes:
            ac, cc = await _agents_count(a)
            scores.append((ac, cc, a))

        scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
        canonical = scores[0][2]
        duplicates = [s[2] for s in scores[1:]]

        logger.warning(
            f"Found {len(app_nodes)} App nodes under Root; deduplicating, keeping canonical App {canonical.id}"
        )

        for dup in duplicates:
            try:
                agents_node = None
                for node in await dup.nodes():
                    if isinstance(node, Agents):
                        agents_node = node
                        break

                if agents_node and agents_node.total_agents > 0:
                    logger.error(
                        f"Skipping deduplication of App {dup.id}: has Agents with "
                        f"total_agents={agents_node.total_agents}. Manual intervention required."
                    )
                    continue

                await root.disconnect(dup)
                await dup.delete(cascade=True)
                logger.debug(f"Removed duplicate App node: {dup.id}")
            except Exception as e:
                logger.warning(f"Failed to remove duplicate App {dup.id}: {e}")

        return [canonical]

    def _apply_app_properties(self, app: App, properties: Dict[str, Any]) -> None:
        """Apply property overrides from descriptor to app instance."""
        _apply_properties(app, properties, reserved={"id", "name", "update_mode"})

    async def _ensure_agents_node(self, app: App) -> Optional[Agents]:
        """Ensure Agents manager node exists.

        Args:
            app: App instance

        Returns:
            Agents instance if successful, None otherwise
        """
        try:
            # Check for existing Agents node
            app_connected_nodes = await app.nodes()
            for node in app_connected_nodes:
                if isinstance(node, Agents):
                    return node

            # Create new Agents node
            agents = await Agents.create(total_agents=0, active_agents=0)
            logger.debug(f"Created Agents node: {agents.id}")

            # Connect to App
            if not await app.is_connected_to(agents):
                await app.connect(agents)

            return agents
        except Exception as e:
            logger.error(f"Error in _ensure_agents_node: {e}", exc_info=True)
            return None

    async def _recount_agent_statistics(self, app: App) -> None:
        """Recount total_agents and active_agents from the live graph.

        Called after all agent installs/updates are complete so that
        counts reflect actual DB state rather than accumulated increments.
        """
        try:
            agents_manager = await app.node(node="Agents")
            if not agents_manager:
                return

            # Count only agents connected to THIS app's Agents manager, not every
            # Agent in the database. Agent.find({}) is global — in a shared or
            # embedded DB (jvagent embedded in a host jvspatial app, or several
            # apps sharing one store) it counts other apps' agents and corrupts
            # this app's totals. AUDIT-core (M21).
            connected = await agents_manager.get_connected_agents()
            agents_manager.total_agents = len(connected)
            agents_manager.active_agents = sum(1 for a in connected if a.enabled)
            await agents_manager.save()
        except Exception as e:
            logger.warning(f"Could not recount agent statistics: {e}")

    async def _install_agents(
        self, descriptor: AppDescriptor, update_mode: Optional[str]
    ) -> None:
        """Install agents specified in app.yaml.

        Agents are referenced using the format: namespace/agent_name
        Agents are discovered from the agents/ folder structure.
        Each agent's actions will be loaded from its agent.yaml file.

        Args:
            descriptor: App descriptor with agent list
            update_mode: "merge" for non-destructive, "source" for destructive, None to skip
        """
        installed_count = 0
        updated_count = 0
        failed_count = 0

        for agent_ref in descriptor.agents:
            # Parse namespace/agent_name format
            if "/" not in agent_ref:
                logger.warning(
                    f"Invalid agent reference format (expected 'namespace/agent_name'): {agent_ref}"
                )
                failed_count += 1
                continue

            namespace, agent_name = agent_ref.split("/", 1)

            agent = await self.agent_loader.install_agent(
                namespace, agent_name, update_mode=update_mode
            )

            if agent:
                if update_mode is not None:
                    updated_count += 1
                    logger.debug(
                        f"  ✓ Agent: {namespace}/{agent_name} (updated - {update_mode})"
                    )
                else:
                    installed_count += 1
                    logger.debug(f"  ✓ Agent: {namespace}/{agent_name}")
            else:
                logger.error(f"Failed to install agent: {namespace}/{agent_name}")
                failed_count += 1

        # Log summary (debug logs for individual agents already shown above)
        if failed_count > 0:
            logger.warning(
                f"Agents: {installed_count} installed, {updated_count} updated, {failed_count} failed"
            )
        elif installed_count > 0 or updated_count > 0:
            parts = []
            if installed_count > 0:
                parts.append(f"{installed_count} installed")
            if updated_count > 0:
                parts.append(f"{updated_count} updated")
            logger.info(f"Agents: {', '.join(parts)}")

    async def get_app_status(self) -> Dict[str, Any]:
        """Get the current application status.

        Returns:
            Dictionary with application status information
        """
        try:
            app = await App.get()
            if not app:
                return {"status": "not_initialized", "message": "App node not found"}

            # Get Agents manager
            agents_manager = await app.node(node="Agents")

            if not agents_manager:
                return {
                    "status": "partial",
                    "message": "App node exists but Agents manager not found",
                    "app": {
                        "id": app.id,
                        "name": app.name,
                        "version": app.version,
                        "update_mode": getattr(app, "update_mode", "run"),
                    },
                }

            # Get agent statistics
            agents_list = (
                await agents_manager.list_agents()
                if hasattr(agents_manager, "list_agents")
                else []
            )

            return {
                "status": "ready",
                "app": {
                    "id": app.id,
                    "name": app.name,
                    "version": app.version,
                    "description": app.description,
                    "file_storage_enabled": app.file_storage_enabled,
                    "update_mode": getattr(app, "update_mode", "run"),
                },
                "agents": {
                    "total": agents_manager.total_agents,
                    "active": agents_manager.active_agents,
                    "list": agents_list,
                },
            }

        except Exception as e:
            return {"status": "error", "message": f"Error getting app status: {str(e)}"}
