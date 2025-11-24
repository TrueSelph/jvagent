"""Action loader for dynamic action discovery and instantiation.

This module provides functionality to discover, load, and instantiate actions
from the filesystem based on their info.yaml descriptors.
"""

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from jvagent.action.action import Action

logger = logging.getLogger(__name__)


class ActionMetadata:
    """Container for action metadata loaded from info.yaml.

    Format:
    - package: object containing package information
      - name: namespace/action_name
      - author: author name
      - archetype: Action class name (same as Action Node class)
      - version: package version
      - meta: object with title, description, group, type
      - config: configuration object
      - dependencies: object with jvagent and actions dependencies

    Configuration should be defined as Pydantic fields on the Action class.
    """

    def __init__(self, data: Dict[str, Any], path: Path, namespace: str = ""):
        """Initialize action metadata.

        Args:
            data: Parsed YAML data from info.yaml
            path: Path to the action directory
            namespace: Namespace for the action (from folder structure)
        """
        self.data = data
        self.path = path
        self.namespace = namespace

        # Extract package information
        package = data.get("package", {})
        if not isinstance(package, dict):
            package = {}

        # Extract name from package.name (namespace/action_name format)
        # or use folder structure namespace + name field
        package_name = package.get("name", "")
        if package_name and "/" in package_name:
            # Parse namespace/action_name from package.name
            parsed_namespace, parsed_name = package_name.split("/", 1)
            # Use parsed namespace if it matches folder structure, otherwise use folder namespace
            if parsed_namespace == namespace or not namespace:
                self.namespace = parsed_namespace
            self.name = parsed_name
        else:
            # Use folder structure namespace and extract name from package or data
            self.name = package.get("name", data.get("name", ""))
            if not self.name:
                # Use directory name
                self.name = path.name

        # Package metadata
        self.author = package.get("author", "")
        self.archetype = package.get("archetype", "Action")
        self.version = package.get("version", "0.0.1")

        # Extract meta information
        meta = package.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        self.title = meta.get("title", self.name.replace("_", " ").title())
        self.description = meta.get("description", "")
        self.group = meta.get("group", "")
        self.type = meta.get("type", "action")

        # Configuration
        self.config = package.get("config", {})

        # Dependencies
        self.dependencies = package.get("dependencies", {})

        # Module is derived from action name (action_name.py)
        self.module = self.name

        # Class name is the archetype
        self.class_name = self.archetype

        # Enabled defaults to True (can be overridden in agent.yaml)
        self.enabled = True

    def __repr__(self) -> str:
        return (
            f"ActionMetadata(namespace={self.namespace}, name={self.name}, version={self.version})"
        )


class ActionLoader:
    """Loader for discovering and instantiating actions from the filesystem."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize the action loader.

        Args:
            base_path: Base path to search for actions. If None, uses current directory.
        """
        self.base_path = Path(base_path or os.getcwd())

    def pre_import_action_modules(self) -> None:
        """Pre-import all action class files from action directories.

        This ensures that all Action subclasses are imported before any queries
        that use _collect_class_names() (which relies on __subclasses__()).

        Scans all agents in the app directory and imports action class files directly,
        using the same approach as load_action_class().
        """
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            logger.debug(f"Agents directory not found: {agents_path}")
            return

        imported_count = 0

        # Iterate through all agent directories
        for agent_namespace_dir in agents_path.iterdir():
            if not agent_namespace_dir.is_dir():
                continue

            # Iterate through agent directories within each namespace
            for agent_dir in agent_namespace_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                # Look for actions directory
                agent_actions_path = agent_dir / "actions"
                if not agent_actions_path.exists() or not agent_actions_path.is_dir():
                    continue

                # Iterate through namespace directories in actions folder
                for action_namespace_dir in agent_actions_path.iterdir():
                    if not action_namespace_dir.is_dir():
                        continue

                    # Iterate through action directories
                    for action_dir in action_namespace_dir.iterdir():
                        if not action_dir.is_dir():
                            continue

                        # Look for info.yaml to get action metadata
                        info_file = action_dir / "info.yaml"
                        if not info_file.exists():
                            continue

                        # Load metadata to get action name and archetype
                        try:
                            with open(info_file, "r", encoding="utf-8") as f:
                                data = yaml.safe_load(f)

                            if not data:
                                continue

                            package = data.get("package", {})
                            if not isinstance(package, dict):
                                continue

                            # Get action name and archetype (class name)
                            package_name = package.get("name", "")
                            if package_name and "/" in package_name:
                                _, action_name = package_name.split("/", 1)
                            else:
                                action_name = package.get("name", action_dir.name)

                            archetype = package.get("archetype", "Action")

                            # Import the action class file directly (same as load_action_class)
                            action_file = action_dir / f"{action_name}.py"
                            if not action_file.exists():
                                continue

                            # Use the same module naming convention as load_action_class
                            module_name = (
                                f"jvagent.actions.{action_namespace_dir.name}.{action_name}"
                            )

                            # Load the module
                            spec = importlib.util.spec_from_file_location(module_name, action_file)

                            if spec is None or spec.loader is None:
                                logger.debug(f"Could not load spec for module: {action_file}")
                                continue

                            # Execute the module to register the Action subclass
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[spec.name] = module
                            spec.loader.exec_module(module)

                            # Verify the class exists and is an Action subclass
                            action_class = getattr(module, archetype, None)
                            if action_class and issubclass(action_class, Action):
                                imported_count += 1
                                # Individual action pre-import logs removed - summary is logged

                        except Exception as e:
                            logger.warning(f"Error pre-importing action from {action_dir}: {e}")
                            continue

        if imported_count > 0:
            logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")

    def discover_actions(self, namespace: str, agent_name: str) -> List[ActionMetadata]:
        """Discover all actions for a given agent.

        Scans the agents/{namespace}/{agent_name}/actions directory for namespace subdirectories,
        then within each namespace for action directories containing info.yaml files.

        Directory structure:
        agents/{namespace}/{agent_name}/actions/{namespace}/{action_name}/info.yaml

        Example:
        agents/jvagent/example_agent/actions/jvagent/example_action/info.yaml
        agents/jvagent/example_agent/actions/contrib/custom_action/info.yaml

        Args:
            namespace: Namespace of the agent
            agent_name: Name of the agent to discover actions for

        Returns:
            List of ActionMetadata objects for discovered actions
        """
        actions_path = self.base_path / "agents" / namespace / agent_name / "actions"

        if not actions_path.exists() or not actions_path.is_dir():
            return []

        discovered = []
        logger.debug(f"Discovering actions from: {actions_path}")

        # Iterate through namespace directories in the actions folder
        for namespace_dir in actions_path.iterdir():
            if not namespace_dir.is_dir():
                continue

            namespace = namespace_dir.name

            # Iterate through action directories within each namespace
            for action_dir in namespace_dir.iterdir():
                if not action_dir.is_dir():
                    continue

                # Look for info.yaml file
                info_file = action_dir / "info.yaml"
                if not info_file.exists():
                    continue

                # Load and parse the info.yaml file
                try:
                    with open(info_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)

                    if not data:
                        continue

                    # Resolve environment variable placeholders
                    from jvagent.core.env_resolver import resolve_env_placeholders

                    data = resolve_env_placeholders(data)

                    # Create metadata object with namespace
                    metadata = ActionMetadata(data, action_dir, namespace=namespace)
                    discovered.append(metadata)

                except Exception as e:
                    # Log error but continue discovering other actions
                    logger.warning(f"Error loading action metadata from {info_file}: {e}")
                    continue

        return discovered

    def load_action_class(self, metadata: ActionMetadata) -> Optional[Type[Action]]:
        """Load the action class from its module.

        Supports loading from either:
        - A package with __init__.py (preferred, allows endpoint discovery)
        - A single module file (backward compatible)

        Args:
            metadata: Action metadata containing module and class information

        Returns:
            Action class if successfully loaded, None otherwise
        """
        if not metadata.module:
            return None

        # Construct paths
        init_file = metadata.path / "__init__.py"
        module_file = metadata.path / f"{metadata.module}.py"

        # Try loading as package first (if __init__.py exists)
        if init_file.exists():
            try:
                # Load the package (__init__.py will be executed, importing endpoints)
                module_name = f"jvagent.actions.{metadata.namespace}.{metadata.name}"
                spec = importlib.util.spec_from_file_location(module_name, init_file)

                if spec is None or spec.loader is None:
                    logger.warning(f"Could not load spec for package: {init_file}")
                    # Fall through to try module file
                else:
                    package = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = package
                    spec.loader.exec_module(package)

                    # Get the action class from the package
                    # It should be imported in __init__.py
                    action_class = getattr(package, metadata.class_name, None)

                    if action_class is None:
                        # Try getting from the module file if not in package
                        if module_file.exists():
                            module_spec = importlib.util.spec_from_file_location(
                                f"{module_name}.{metadata.module}", module_file
                            )
                            if module_spec and module_spec.loader:
                                module = importlib.util.module_from_spec(module_spec)
                                module_spec.loader.exec_module(module)
                                action_class = getattr(module, metadata.class_name, None)
                                # Also make it available on the package
                                setattr(package, metadata.class_name, action_class)

                    if action_class is not None:
                        # Verify it's a subclass of Action
                        if not issubclass(action_class, Action):
                            logger.warning(
                                f"Class {metadata.class_name} is not a subclass of Action"
                            )
                            return None
                        return action_class
            except Exception as e:
                logger.warning(f"Error loading package from {init_file}: {e}")
                # Fall through to try module file

        # Fall back to loading module file directly (backward compatible)
        if not module_file.exists():
            logger.warning(f"Module file not found: {module_file}")
            return None

        try:
            # Load the module dynamically with namespace in the module name
            module_name = f"jvagent.actions.{metadata.namespace}.{metadata.name}"
            spec = importlib.util.spec_from_file_location(module_name, module_file)

            if spec is None or spec.loader is None:
                logger.warning(f"Could not load spec for module: {module_file}")
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Get the action class from the module
            action_class = getattr(module, metadata.class_name, None)

            if action_class is None:
                logger.warning(f"Class {metadata.class_name} not found in module {module_file}")
                return None

            # Verify it's a subclass of Action
            if not issubclass(action_class, Action):
                logger.warning(f"Class {metadata.class_name} is not a subclass of Action")
                return None

            return action_class

        except Exception as e:
            logger.error(f"Error loading action class from {module_file}: {e}", exc_info=True)
            return None

    def create_action_instance(
        self,
        metadata: ActionMetadata,
        agent_id: str,
        agent_name: str,
        action_class: Optional[Type[Action]] = None,
        property_overrides: Optional[Dict[str, Any]] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Action]:
        """Create an instance of an action.

        All configuration is handled through property overrides. There is no
        separate config dictionary - all settings should be typed Pydantic fields.

        Args:
            metadata: Action metadata
            agent_id: ID of the agent this action belongs to
            agent_name: Name of the agent (for filesystem paths)
            action_class: Action class to instantiate (if None, will be loaded)
            property_overrides: Property overrides from agent.yaml

        Returns:
            Action instance if successful, None otherwise
        """
        # Load action class if not provided
        if action_class is None:
            action_class = self.load_action_class(metadata)
            if action_class is None:
                return None

        try:
            # Build initial action data
            action_data = {
                "agent_id": agent_id,
                "namespace": metadata.namespace,
                "label": metadata.name,
                "description": metadata.description,
                "enabled": metadata.enabled,
            }

            # Apply property overrides from agent.yaml
            # This includes both standard overrides and any custom properties
            if property_overrides:
                for key, value in property_overrides.items():
                    # Only override public properties (not private, not metadata)
                    if not key.startswith("_") and key not in ["id", "agent_id", "namespace"]:
                        action_data[key] = value

            # Create action instance with metadata
            action = action_class(**action_data)

            # Merge config: base config from info.yaml + overrides from agent.yaml
            merged_config = dict(metadata.config) if metadata.config else {}
            if config_overrides:
                merged_config.update(config_overrides)

            # Store metadata in private field (including agent_name for path construction)
            action._metadata = {
                "name": metadata.name,
                "title": metadata.title,
                "namespace": metadata.namespace,
                "version": metadata.version,
                "module": metadata.module,
                "module_root": str(metadata.path),
                "class": metadata.class_name,
                "archetype": metadata.archetype,
                "author": metadata.author,
                "group": metadata.group,
                "type": metadata.type,
                "config": merged_config,
                "config_overrides": config_overrides or {},
                "dependencies": metadata.dependencies,
                "agent_name": agent_name,
            }

            return action

        except Exception as e:
            logger.error(f"Error creating action instance for {metadata.name}: {e}", exc_info=True)
            return None

    def load_actions_for_agent(
        self,
        namespace: str,
        agent_name: str,
        agent_id: str,
        action_configs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Action]:
        """Load all actions for an agent.

        All configuration is applied through context properties. Actions are
        referenced using the format: namespace/action_name

        Args:
            namespace: Namespace of the agent (for filesystem path)
            agent_name: Name of the agent (for filesystem path)
            agent_id: ID of the agent node
            action_configs: Optional list of action configurations from agent.yaml

        Returns:
            List of action instances
        """
        # Discover available actions from filesystem
        discovered = self.discover_actions(namespace, agent_name)

        # Build action config lookup (using namespace/action_name format)
        # Resolve environment variable placeholders in action configs
        from jvagent.core.env_resolver import resolve_env_placeholders

        config_lookup = {}
        if action_configs:
            for cfg in action_configs:
                # Resolve environment variables in action config
                cfg = resolve_env_placeholders(cfg)

                # Action reference: "namespace/action_name" in 'action' field
                action_ref = cfg.get("action")

                if not action_ref:
                    logger.warning(f"Action config missing 'action' field: {cfg}")
                    continue

                # Parse namespace/action_name format
                if "/" not in action_ref:
                    logger.warning(
                        f"Invalid action reference format (expected 'namespace/action_name'): {action_ref}"
                    )
                    continue

                namespace, action_name = action_ref.split("/", 1)
                key = f"{namespace}/{action_name}"
                config_lookup[key] = cfg

        # Load and instantiate actions
        actions = []
        for metadata in discovered:
            # Find config using namespace/action_name format
            full_key = f"{metadata.namespace}/{metadata.name}"
            action_cfg = config_lookup.get(full_key, {})

            # Extract property overrides from 'context' field
            property_overrides = {}
            config_overrides = {}

            # Properties are in 'context'
            if "context" in action_cfg:
                context = action_cfg["context"]
                if isinstance(context, dict):
                    # Extract enabled separately (not a property override)
                    if "enabled" in context:
                        metadata.enabled = context["enabled"]
                        # Create context copy without enabled
                        context_without_enabled = {
                            k: v for k, v in context.items() if k != "enabled"
                        }
                        property_overrides.update(context_without_enabled)
                    else:
                        property_overrides.update(context)

            # Config overrides are in separate 'config' field
            if "config" in action_cfg:
                config_overrides = action_cfg["config"]
                if not isinstance(config_overrides, dict):
                    config_overrides = {}

            # Create action instance (pass agent_name for metadata)
            action = self.create_action_instance(
                metadata,
                agent_id,
                agent_name,
                property_overrides=property_overrides,
                config_overrides=config_overrides,
            )

            if action:
                actions.append(action)

        return actions
