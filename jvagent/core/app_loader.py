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

from jvagent.core.agent import Agent  # noqa: F401
from jvagent.core.agent_loader import AgentLoader
from jvagent.core.agents import Agents
from jvagent.core.app import App
from jvagent.core.bootstrap_logger import BootstrapLogger

logger = logging.getLogger(__name__)


class AppDescriptor:
    """Container for application metadata loaded from app.yaml.

    Format:
    - app: app_name (application identifier)
    - version: at top level
    - author: at top level
    - context: object containing App node properties (name, description, file_storage_*, etc.)
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

        # Extract properties from context object
        context = data.get("context", {})
        if not isinstance(context, dict):
            context = {}

        self.name = context.get("name", "jvagent Application")
        self.description = context.get("description", "")
        self.file_storage_provider = context.get("file_storage_provider", "local")
        self.file_storage_root_dir = context.get("file_storage_root_dir", ".files")
        self.file_storage_enabled = context.get("file_storage_enabled", True)

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
                print(f"Warning: Invalid agent reference format (expected string): {agent_ref}")

    def __repr__(self) -> str:
        return f"AppDescriptor(id={self.app_id}, name={self.name}, version={self.version})"


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
            print(f"App descriptor not found: {app_file}")
            return None

        try:
            with open(app_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                print(f"Empty app descriptor: {app_file}")
                return None

            # Resolve environment variable placeholders
            from jvagent.core.env_resolver import resolve_env_placeholders

            data = resolve_env_placeholders(data)

            return AppDescriptor(data, self.base_path)

        except Exception as e:
            print(f"Error loading app descriptor from {app_file}: {e}")
            return None

    async def bootstrap_application(self, update_if_exists: bool = False) -> Optional[App]:
        """Bootstrap the application from app.yaml.

        This method:
        1. Ensures Root node exists
        2. Creates or updates App node based on app.yaml
        3. Creates Agents manager node
        4. Installs all agents specified in app.yaml

        Args:
            update_if_exists: If True, update existing entities; if False, skip existing

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
            # Step 0: Pre-import all action __init__.py modules
            # This ensures Action subclasses are available for _collect_class_names()
            # before any queries are executed
            from jvagent.action.action_loader import ActionLoader

            action_loader = ActionLoader(base_path=str(self.base_path))
            action_loader.pre_import_action_modules()

            # Step 1: Ensure Root node exists
            root = await Root.get()
            if root is None:
                logger.error("Failed to get Root node")
                return None

            # Step 2: Create or update App node
            app = await self._ensure_app_node(descriptor, update_if_exists)
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
                await self._install_agents(descriptor, update_if_exists)

            bootstrap_log.complete()
            return app

        except Exception as e:
            logger.error(f"Error bootstrapping application: {e}", exc_info=True)
            return None

    async def _ensure_app_node(
        self, descriptor: AppDescriptor, update_if_exists: bool
    ) -> Optional[App]:
        """Ensure App node exists and is configured.

        Args:
            descriptor: App descriptor
            update_if_exists: If True, update existing app; if False, skip update

        Returns:
            App instance if successful, None otherwise
        """
        try:
            # Check for existing App node
            existing_apps = await App.find({"context.name": descriptor.name})

            if existing_apps:
                app = existing_apps[0]

                if update_if_exists:
                    # Update existing app with context properties
                    app.name = descriptor.name
                    app.version = descriptor.version
                    app.description = descriptor.description
                    app.file_storage_provider = descriptor.file_storage_provider
                    app.file_storage_root_dir = descriptor.file_storage_root_dir
                    app.file_storage_enabled = descriptor.file_storage_enabled

                    # Apply additional context properties
                    self._apply_app_properties(app, descriptor.properties)

                    await app.save()
                    logger.debug(f"Updated App node: {app.id}")

                # Update cache
                App._cached_app = app
                return app

            # Build initial app data
            app_data = {
                "name": descriptor.name,
                "version": descriptor.version,
                "description": descriptor.description,
                "file_storage_provider": descriptor.file_storage_provider,
                "file_storage_root_dir": descriptor.file_storage_root_dir,
                "file_storage_enabled": descriptor.file_storage_enabled,
            }

            # Apply additional context properties
            if descriptor.properties:
                for key, value in descriptor.properties.items():
                    # Only override if it's a valid field (don't override private fields)
                    if not key.startswith("_") and key not in ["id", "name"]:
                        app_data[key] = value

            # Create new App node
            app = await App.create(**app_data)
            logger.debug(f"Created App node: {app.id}")

            # Update cache
            App._cached_app = app
            return app
        except Exception as e:
            logger.error(f"Error in _ensure_app_node: {e}", exc_info=True)
            return None

    def _apply_app_properties(self, app: App, properties: Dict[str, Any]) -> None:
        """Apply property overrides from descriptor to app instance.

        Args:
            app: App instance to update
            properties: Dictionary with property overrides
        """
        if not properties:
            return

        for key, value in properties.items():
            # Only set public properties (not private, not id, not name - name is static)
            if not key.startswith("_") and key not in ["id", "name"] and hasattr(app, key):
                try:
                    setattr(app, key, value)
                    logger.debug(f"Set app.{key} = {value}")
                except Exception as e:
                    logger.warning(f"Could not set app.{key}: {e}")

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

    async def _install_agents(self, descriptor: AppDescriptor, update_if_exists: bool) -> None:
        """Install agents specified in app.yaml.

        Agents are referenced using the format: namespace/agent_name
        Agents are discovered from the agents/ folder structure.
        Each agent's actions will be loaded from its agent.yaml file.

        Args:
            descriptor: App descriptor with agent list
            update_if_exists: If True, update existing agents; if False, skip existing
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

            # Check if agent already exists
            existing_agents = await Agent.find(
                {"context.name": agent_name, "context.namespace": namespace}
            )
            was_existing = bool(existing_agents)

            # Install the agent (this will also load actions from agent.yaml)
            # Pass update_if_exists to ensure agent properties are updated
            agent = await self.agent_loader.install_agent(namespace, agent_name, update_if_exists)

            if agent:
                if was_existing and update_if_exists:
                    updated_count += 1
                    logger.debug(f"  ✓ Agent: {namespace}/{agent_name} (updated)")
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
            app_nodes = await app.nodes()
            agents_manager = None
            for node in app_nodes:
                if isinstance(node, Agents):
                    agents_manager = node
                    break

            if not agents_manager:
                return {
                    "status": "partial",
                    "message": "App node exists but Agents manager not found",
                    "app": {
                        "id": app.id,
                        "name": app.name,
                        "version": app.version,
                    },
                }

            # Get agent statistics
            agents_list = (
                await agents_manager.list_agents() if hasattr(agents_manager, "list_agents") else []
            )

            return {
                "status": "ready",
                "app": {
                    "id": app.id,
                    "name": app.name,
                    "version": app.version,
                    "description": app.description,
                    "file_storage_enabled": app.file_storage_enabled,
                },
                "agents": {
                    "total": agents_manager.total_agents,
                    "active": agents_manager.active_agents,
                    "list": agents_list,
                },
            }

        except Exception as e:
            return {"status": "error", "message": f"Error getting app status: {str(e)}"}
