"""Action loader for dynamic action discovery and instantiation.

This module provides functionality to discover, load, and instantiate actions
from the filesystem based on their info.yaml descriptors.
"""

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from jvagent.action.base import Action
from jvagent.core.dependency_installer import install_action_dependencies
from jvagent.core.env_resolver import resolve_env_placeholders

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
      - dependencies: object with jvagent, actions, and pip dependencies
        - jvagent: jvagent version requirement (e.g., "~2.1.0")
        - actions: list of action dependencies (by namespace/action_name)
        - pip: list of pip package specifications (e.g., ["requests>=2.25.0", "numpy"])

    Pip dependencies are automatically installed before the action is loaded.

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

        # Core action flags (set by discover_core_action)
        self.is_core_action: bool = False
        self.core_module_path: Optional[str] = None
        self.core_class_name: Optional[str] = None

        # Agent information (set when discovering actions for a specific agent)
        self.agent_namespace: Optional[str] = None
        self.agent_name: Optional[str] = None

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
        self._core_action_path: Optional[Path] = None
        self._core_action_cache: Optional[Dict[str, Dict[str, Any]]] = None

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _has_info_yaml_files(self, path: Path) -> bool:
        """Check if a directory contains info.yaml files (indicating it's an action directory).

        Args:
            path: Directory path to check

        Returns:
            True if info.yaml files are found, False otherwise
        """
        return any(
            info_file.exists()
            for info_file in path.rglob("info.yaml")
            if "__pycache__" not in info_file.parts
            and not any(part.startswith("_") for part in info_file.parts[:-1])
        )

    def _load_info_yaml(self, info_file: Path) -> Optional[Dict[str, Any]]:
        """Load and parse info.yaml file with environment variable resolution.

        Args:
            info_file: Path to info.yaml file

        Returns:
            Parsed YAML data with resolved environment variables, or None if failed
        """
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                return None

            # Resolve environment variable placeholders
            return resolve_env_placeholders(data)

        except Exception as e:
            logger.debug(f"Error loading info.yaml from {info_file}: {e}")
            return None

    def _extract_action_name(self, package: Dict[str, Any], action_dir: Path) -> str:
        """Extract action name from package data.

        Args:
            package: Package dictionary from info.yaml
            action_dir: Action directory path (used as fallback)

        Returns:
            Action name string
        """
        package_name = package.get("name", "")
        if package_name and "/" in package_name:
            _, action_name = package_name.split("/", 1)
            return action_name
        else:
            return package.get("name", action_dir.name)

    def _ensure_dependencies_installed(
        self, data: Dict[str, Any], action_name: str, action_dir: Path
    ) -> None:
        """Install pip dependencies for an action if specified in info.yaml.

        Args:
            data: Parsed info.yaml data
            action_name: Name of the action (for logging)
            action_dir: Action directory path
        """
        try:
            install_action_dependencies(data, action_name, action_dir)
        except Exception as e:
            logger.warning(f"Error installing dependencies for {action_name}: {e}")

    def _load_action_module(
        self,
        module_name: str,
        action_dir: Path,
        action_name: str,
        archetype: str,
    ) -> Optional[Type[Action]]:
        """Load an action class from a module or package.

        Tries to load as a package (if __init__.py exists) first, then falls back
        to loading the module file directly. This ensures endpoints are imported
        when loading packages.

        Args:
            module_name: Full module name (e.g., "jvagent.actions.namespace.action_name")
            action_dir: Directory containing the action
            action_name: Name of the action (for constructing file paths)
            archetype: Class name to load

        Returns:
            Action class if successfully loaded, None otherwise
        """
        init_file = action_dir / "__init__.py"
        module_file = action_dir / f"{action_name}.py"

        # Try loading as package first (if __init__.py exists)
        # This ensures __init__.py executes, which imports endpoints
        if init_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(module_name, init_file)

                if spec is None or spec.loader is None:
                    logger.debug(f"Could not load spec for package: {init_file}")
                    # Fall through to try module file
                else:
                    package = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = package
                    try:
                        spec.loader.exec_module(package)
                    except (ImportError, NameError, ModuleNotFoundError) as e:
                        logger.warning(
                            f"Error importing action package {init_file}: {e}. "
                            f"This may be due to missing dependencies or import errors."
                        )
                        # Fall through to try module file
                    else:
                        # Get the action class from the package
                        # It should be imported in __init__.py
                        action_class = getattr(package, archetype, None)

                        if action_class is None:
                            # Try getting from the module file if not in package
                            if module_file.exists():
                                module_spec = importlib.util.spec_from_file_location(
                                    f"{module_name}.{action_name}", module_file
                                )
                                if module_spec and module_spec.loader:
                                    module = importlib.util.module_from_spec(module_spec)
                                    module_spec.loader.exec_module(module)
                                    action_class = getattr(module, archetype, None)
                                    # Also make it available on the package
                                    if action_class:
                                        setattr(package, archetype, action_class)

                        if action_class is not None:
                            # Verify it's a subclass of Action
                            if not issubclass(action_class, Action):
                                logger.warning(
                                    f"Class {archetype} is not a subclass of Action"
                                )
                                return None
                            return action_class
            except Exception as e:
                logger.warning(f"Error loading package from {init_file}: {e}")
                # Fall through to try module file

        # Fall back to loading module file directly
        if not module_file.exists():
            return None

        try:
            spec = importlib.util.spec_from_file_location(module_name, module_file)

            if spec is None or spec.loader is None:
                logger.debug(f"Could not load spec for module: {module_file}")
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Get the action class from the module
            action_class = getattr(module, archetype, None)

            if action_class is None:
                logger.warning(f"Class {archetype} not found in module {module_file}")
                return None

            # Verify it's a subclass of Action
            if not issubclass(action_class, Action):
                logger.warning(f"Class {archetype} is not a subclass of Action")
                return None

            return action_class

        except Exception as e:
            logger.error(f"Error loading action class from {module_file}: {e}", exc_info=True)
            return None

    # ============================================================================
    # Core Action Path and Discovery
    # ============================================================================

    def _get_core_action_path(self) -> Optional[Path]:
        """Get the path to the core jvagent action directory.

        Checks both installed package location and development directory.
        Always re-validates cached path to ensure it still exists (important for app restarts).

        Returns:
            Path to jvagent/action directory, or None if not found
        """
        # Always re-validate cached path to handle app restarts and path changes
        if self._core_action_path is not None and self._core_action_path.exists():
            # Verify it's still the right directory by checking for info.yaml files
            if self._has_info_yaml_files(self._core_action_path):
                return self._core_action_path
            else:
                # Cached path is invalid, reset it
                self._core_action_path = None
                self._core_action_cache = None  # Also reset the action cache

        # Try to find via importlib (installed package)
        try:
            spec = importlib.util.find_spec("jvagent")
            if spec and spec.origin:
                # spec.origin points to __init__.py, get parent directory
                jvagent_path = Path(spec.origin).parent
                action_path = jvagent_path / "action"
                if action_path.exists() and action_path.is_dir():
                    self._core_action_path = action_path
                    logger.debug(f"Found core action path (installed): {action_path}")
                    return action_path
        except Exception as e:
            logger.debug(f"Could not find jvagent via importlib: {e}")

        # Try development directory (parent of base_path)
        dev_paths = [
            self.base_path.parent / "jvagent" / "action",
            self.base_path.parent.parent / "jvagent" / "jvagent" / "action",
            Path(__file__).parent,  # Current file is in jvagent/action/
        ]

        for dev_path in dev_paths:
            if dev_path.exists() and dev_path.is_dir():
                # Verify it's the right directory by checking for info.yaml files
                if self._has_info_yaml_files(dev_path):
                    self._core_action_path = dev_path
                    logger.debug(f"Found core action path (dev): {dev_path}")
                    return dev_path

        logger.debug("Could not find core action path")
        return None

    def _build_core_action_cache(self) -> Dict[str, Dict[str, Any]]:
        """Dynamically build a cache of core actions by scanning for info.yaml files.

        Scans all subdirectories in the core action path for info.yaml files and
        builds a mapping from action_name to action metadata.

        Returns:
            Dictionary mapping action names to action metadata (dir, module_file, class_name, relative_path)
        """
        if self._core_action_cache is not None:
            return self._core_action_cache

        core_path = self._get_core_action_path()
        if not core_path:
            self._core_action_cache = {}
            return self._core_action_cache

        action_cache = {}

        # Recursively scan for info.yaml files
        for info_file in core_path.rglob("info.yaml"):
            # Skip if in __pycache__ or hidden directories
            if "__pycache__" in info_file.parts or any(part.startswith("_") for part in info_file.parts[:-1]):
                continue

            # Load info.yaml using helper
            data = self._load_info_yaml(info_file)
            if not data:
                continue

            package = data.get("package", {})
            if not isinstance(package, dict):
                continue

            # Extract action name from package.name (format: "jvagent/action_name")
            full_name = package.get("name", "")
            if "/" not in full_name:
                continue

            namespace_part, action_name = full_name.split("/", 1)
            if namespace_part != "jvagent":
                continue

            # Get class name from archetype
            class_name = package.get("archetype", "")
            if not class_name:
                continue

            # Get the directory containing the info.yaml
            action_dir = info_file.parent

            # Determine module file name (look for Python files in the directory)
            # Prefer base.py if it exists (common pattern), otherwise use first non-__init__.py file
            module_file = None
            base_file = action_dir / "base.py"
            if base_file.exists():
                module_file = "base"
            else:
                for py_file in action_dir.glob("*.py"):
                    if py_file.name != "__init__.py":
                        module_file = py_file.stem
                        break

            # If no Python file found, use directory name as fallback
            if not module_file:
                module_file = action_dir.name

            # Build relative path from core_path
            try:
                relative_path = action_dir.relative_to(core_path)
                relative_path_str = str(relative_path).replace("\\", "/")
            except ValueError:
                # If relative path can't be computed, use directory name
                relative_path_str = action_dir.name

            # Store parsed data for reuse in discover_core_action
            action_cache[action_name] = {
                "dir": action_dir,
                "module_file": module_file,
                "class_name": class_name,
                "relative_path": relative_path_str,
                "data": data,  # Store parsed data to avoid re-parsing
                "info_file": info_file,  # Store path for reference
            }

            logger.debug(
                f"Discovered core action: {action_name} -> "
                f"(dir={action_dir.name}, module={module_file}, class={class_name}, path={relative_path_str})"
            )

        self._core_action_cache = action_cache
        logger.debug(f"Built core action cache with {len(action_cache)} actions")
        return action_cache

    def discover_core_action(self, namespace: str, action_name: str) -> Optional[ActionMetadata]:
        """Discover a core action from the jvagent library.

        This method always attempts to discover core actions, even on app restart.
        The core action path is re-validated on each call to handle path changes.

        Args:
            namespace: Namespace of the action (should be "jvagent")
            action_name: Name of the action (e.g., "interact_router")

        Returns:
            ActionMetadata if found, None otherwise
        """
        if namespace != "jvagent":
            return None

        # Get core action path (re-validates cached path on each call)
        core_path = self._get_core_action_path()
        if not core_path:
            logger.debug(f"Core action path not found for {namespace}/{action_name}")
            return None

        # Build or get cached action map
        action_cache = self._build_core_action_cache()
        action_info = action_cache.get(action_name)
        if not action_info:
            logger.debug(f"No core action found for: {action_name}")
            return None

        action_dir = action_info["dir"]
        module_file = action_info["module_file"]
        class_name = action_info["class_name"]
        relative_path = action_info["relative_path"]

        # Use cached data if available, otherwise load from file
        if "data" in action_info:
            data = action_info["data"]
        else:
            # Fallback: load from file (shouldn't happen if cache is built correctly)
            info_file = action_dir / "info.yaml"
            if not info_file.exists():
                logger.debug(f"Core action info.yaml not found: {info_file}")
                return None
            data = self._load_info_yaml(info_file)
            if not data:
                return None

        # Ensure archetype matches what we discovered
        package = data.get("package", {})
        if isinstance(package, dict):
            package["archetype"] = class_name

        # Install pip dependencies before creating metadata
        self._ensure_dependencies_installed(data, action_name, action_dir)

        # Create metadata with is_core_action flag
        metadata = ActionMetadata(data, action_dir, namespace=namespace)
        # Mark as core action
        metadata.is_core_action = True
        # Convert relative path (with slashes) to module path (with dots)
        category_module = relative_path.replace("/", ".")
        # If __init__.py exists, use package import (package exports the class)
        # Otherwise, use specific module file import
        init_file = action_dir / "__init__.py"
        if init_file.exists():
            # Use package import path (e.g., jvagent.action.persona)
            metadata.core_module_path = f"jvagent.action.{category_module}"
        else:
            # Use specific module file import (e.g., jvagent.action.persona.persona_action)
            metadata.core_module_path = f"jvagent.action.{category_module}.{module_file}"
        metadata.core_class_name = class_name

        logger.debug(f"Discovered core action: {namespace}/{action_name} from {action_dir}")
        return metadata

    def pre_import_action_modules(self) -> None:
        """Pre-import all action class files from action directories.

        This ensures that all Action subclasses are imported before any queries
        that use _collect_class_names() (which relies on __subclasses__()).
        
        Also pre-imports core action packages to ensure their __init__.py files
        (which import endpoints) are executed. This is critical for endpoint discovery.

        Scans all agents in the app directory and imports action class files directly,
        using the same approach as load_action_class().
        Also scans core actions from the jvagent library.
        """
        imported_count = 0
        
        # First, pre-import core action packages to ensure endpoints are registered
        core_imported = self._pre_import_core_action_packages()
        imported_count += core_imported

        # Then, pre-import local actions from filesystem
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            logger.debug(f"Agents directory not found: {agents_path}")
            if imported_count > 0:
                logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")
            return

        # Iterate through all agent directories
        for agent_namespace_dir in agents_path.iterdir():
            if not agent_namespace_dir.is_dir():
                continue

            agent_namespace = agent_namespace_dir.name

            # Iterate through agent directories within each namespace
            for agent_dir in agent_namespace_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                agent_name = agent_dir.name

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
                            data = self._load_info_yaml(info_file)
                            if not data:
                                continue

                            package = data.get("package", {})
                            if not isinstance(package, dict):
                                continue

                            # Extract action name and archetype using helpers
                            action_name = self._extract_action_name(package, action_dir)
                            archetype = package.get("archetype", "Action")

                            # Install pip dependencies before importing
                            self._ensure_dependencies_installed(data, action_name, action_dir)

                            # Use agent-specific module naming: jvagent.actions.{agent_namespace}.{agent_name}.{action_namespace}.{action_name}
                            module_name = (
                                f"jvagent.actions.{agent_namespace}.{agent_name}."
                                f"{action_namespace_dir.name}.{action_name}"
                            )

                            # Load action module using shared helper
                            action_class = self._load_action_module(
                                module_name, action_dir, action_name, archetype
                            )

                            if action_class is not None:
                                imported_count += 1
                                # Individual action pre-import logs removed - summary is logged

                        except Exception as e:
                            logger.warning(f"Error pre-importing action from {action_dir}: {e}")
                            continue

        if imported_count > 0:
            logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")

    def pre_import_action_modules_for_agents(self, agent_refs: List[str]) -> None:
        """Pre-import action modules only for specified agents from app.yaml.

        This ensures that only agents listed in app.yaml have their modules loaded,
        preventing conflicts and ensuring proper module isolation between agents.

        Args:
            agent_refs: List of agent references in "namespace/agent_name" format
        """
        imported_count = 0

        # First, pre-import core action packages to ensure endpoints are registered
        # Core actions are shared and don't need agent-specific isolation
        core_imported = self._pre_import_core_action_packages()
        imported_count += core_imported

        # Build set of agent paths for quick lookup
        agent_paths = set()
        for agent_ref in agent_refs:
            if "/" not in agent_ref:
                logger.warning(
                    f"Invalid agent reference format (expected 'namespace/agent_name'): {agent_ref}"
                )
                continue
            namespace, agent_name = agent_ref.split("/", 1)
            agent_paths.add((namespace, agent_name))

        if not agent_paths:
            if imported_count > 0:
                logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")
            return

        # Pre-import local actions only for specified agents
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            logger.debug(f"Agents directory not found: {agents_path}")
            if imported_count > 0:
                logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")
            return

        # Iterate through agent directories, but only process those in agent_paths
        for agent_namespace_dir in agents_path.iterdir():
            if not agent_namespace_dir.is_dir():
                continue

            agent_namespace = agent_namespace_dir.name

            # Iterate through agent directories within each namespace
            for agent_dir in agent_namespace_dir.iterdir():
                if not agent_dir.is_dir():
                    continue

                agent_name = agent_dir.name

                # Only process agents listed in app.yaml
                if (agent_namespace, agent_name) not in agent_paths:
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
                            data = self._load_info_yaml(info_file)
                            if not data:
                                continue

                            package = data.get("package", {})
                            if not isinstance(package, dict):
                                continue

                            # Extract action name and archetype using helpers
                            action_name = self._extract_action_name(package, action_dir)
                            archetype = package.get("archetype", "Action")

                            # Install pip dependencies before importing
                            self._ensure_dependencies_installed(data, action_name, action_dir)

                            # Use agent-specific module naming: jvagent.actions.{agent_namespace}.{agent_name}.{action_namespace}.{action_name}
                            module_name = (
                                f"jvagent.actions.{agent_namespace}.{agent_name}."
                                f"{action_namespace_dir.name}.{action_name}"
                            )

                            # Load action module using shared helper
                            action_class = self._load_action_module(
                                module_name, action_dir, action_name, archetype
                            )

                            if action_class is not None:
                                imported_count += 1

                        except Exception as e:
                            logger.warning(f"Error pre-importing action from {action_dir}: {e}")
                            continue

        if imported_count > 0:
            logger.debug(f"Pre-imported {imported_count} action class(es) for class discovery")

    def _pre_import_core_action_packages(self) -> int:
        """Pre-import core action packages to ensure their __init__.py files execute.
        
        This is critical for endpoint discovery - parent package __init__.py files
        import endpoints.py modules which register endpoints via @endpoint decorators.
        
        Also installs pip dependencies for core actions before importing.
        
        Returns:
            Number of core action packages imported
        """
        core_path = self._get_core_action_path()
        if not core_path:
            return 0

        imported_count = 0
        
        # Build core action cache to get all core actions
        action_cache = self._build_core_action_cache()
        
        # First, install dependencies for all core actions
        for action_name, action_info in action_cache.items():
            action_dir = action_info["dir"]
            data = action_info.get("data")
            
            if data:
                # Install pip dependencies before importing
                self._ensure_dependencies_installed(data, action_name, action_dir)
        
        # Then import each core action's parent packages
        # We need to import parent packages, not just the action modules themselves
        # This ensures __init__.py files execute and import endpoints
        imported_packages = set()
        
        for action_name, action_info in action_cache.items():
            relative_path = action_info["relative_path"]
            
            # Build the full module path for the parent package
            # e.g., "model/language/openai" -> "jvagent.action.model.language"
            # We import the parent package, not the module itself
            path_parts = relative_path.split("/")
            
            # Import parent packages (skip the last part which is the module name)
            for i in range(len(path_parts)):
                parent_path = "/".join(path_parts[:i+1])
                package_path = f"jvagent.action.{parent_path.replace('/', '.')}"
                
                # Only import each package once
                if package_path not in imported_packages:
                    try:
                        importlib.import_module(package_path)
                        imported_packages.add(package_path)
                        imported_count += 1
                        logger.debug(f"Pre-imported core action package: {package_path}")
                    except ImportError as e:
                        # Some packages might not have __init__.py, that's okay
                        logger.debug(f"Could not import core package {package_path}: {e}")
                    except Exception as e:
                        logger.warning(f"Error importing core package {package_path}: {e}")
        
        if imported_count > 0:
            logger.debug(f"Pre-imported {imported_count} core action package(s) for endpoint discovery")
        
        return imported_count

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
        agent_namespace = namespace  # Store agent namespace
        actions_path = self.base_path / "agents" / agent_namespace / agent_name / "actions"

        if not actions_path.exists() or not actions_path.is_dir():
            return []

        discovered = []
        logger.debug(f"Discovering actions from: {actions_path}")

        # Iterate through namespace directories in the actions folder
        for namespace_dir in actions_path.iterdir():
            if not namespace_dir.is_dir():
                continue

            action_namespace = namespace_dir.name  # Action namespace (different from agent namespace)

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
                    data = self._load_info_yaml(info_file)
                    if not data:
                        continue

                    # Extract action name using helper
                    package = data.get("package", {})
                    if not isinstance(package, dict):
                        continue

                    action_name = self._extract_action_name(package, action_dir)

                    # Install dependencies
                    self._ensure_dependencies_installed(data, action_name, action_dir)

                    # Create metadata object with action namespace
                    metadata = ActionMetadata(data, action_dir, namespace=action_namespace)
                    # Store agent information for agent-specific module paths
                    metadata.agent_namespace = agent_namespace
                    metadata.agent_name = agent_name
                    discovered.append(metadata)

                except Exception as e:
                    # Log error but continue discovering other actions
                    logger.warning(f"Error loading action metadata from {info_file}: {e}")
                    continue

        return discovered

    def load_action_class(self, metadata: ActionMetadata) -> Optional[Type[Action]]:
        """Load the action class from its module.

        Supports loading from either:
        - Core actions: Uses jvagent.action.* import paths
        - Local actions: Uses jvagent.actions.* import paths (filesystem-based)
        - A package with __init__.py (preferred, allows endpoint discovery)
        - A single module file

        Args:
            metadata: Action metadata containing module and class information

        Returns:
            Action class if successfully loaded, None otherwise
        """
        # Handle core actions differently
        if getattr(metadata, "is_core_action", False) and metadata.core_module_path:
            return self._load_core_action_class(metadata)

        if not metadata.module:
            return None

        # Use agent-specific module path if agent info is available
        # Format: jvagent.actions.{agent_namespace}.{agent_name}.{action_namespace}.{action_name}
        if metadata.agent_namespace and metadata.agent_name:
            module_name = (
                f"jvagent.actions.{metadata.agent_namespace}.{metadata.agent_name}."
                f"{metadata.namespace}.{metadata.name}"
            )
        else:
            # Fallback to old format for backward compatibility (should not happen in normal flow)
            module_name = f"jvagent.actions.{metadata.namespace}.{metadata.name}"
        return self._load_action_module(
            module_name, metadata.path, metadata.name, metadata.class_name
        )

    def reload_action_class(
        self, metadata: ActionMetadata, existing_action: Optional[Action] = None
    ) -> Optional[Type[Action]]:
        """Reload an action class from its module, ensuring fresh code is loaded.

        This method unloads old modules and reloads them fresh, ensuring code changes
        take effect. Used during --update operations to reload action code.

        Note: Module unloading from existing_action should be done by the caller
        before calling this method, as it requires async execution.

        Args:
            metadata: Action metadata containing module and class information
            existing_action: Optional existing action instance (for reference, unloading should be done by caller)

        Returns:
            Reloaded action class if successful, None otherwise
        """
        # Handle core actions differently
        if getattr(metadata, "is_core_action", False) and metadata.core_module_path:
            return self._reload_core_action_class(metadata)

        if not metadata.module:
            return None

        # Use agent-specific module path if agent info is available
        # Format: jvagent.actions.{agent_namespace}.{agent_name}.{action_namespace}.{action_name}
        if metadata.agent_namespace and metadata.agent_name:
            module_name = (
                f"jvagent.actions.{metadata.agent_namespace}.{metadata.agent_name}."
                f"{metadata.namespace}.{metadata.name}"
            )
        else:
            # Fallback to old format for backward compatibility (should not happen in normal flow)
            module_name = f"jvagent.actions.{metadata.namespace}.{metadata.name}"
        return self._unload_and_reload_action_module(
            module_name, metadata.path, metadata.name, metadata.class_name
        )

    def _reload_core_action_class(self, metadata: ActionMetadata) -> Optional[Type[Action]]:
        """Reload a core action class using importlib.reload().

        Args:
            metadata: Action metadata with core_module_path and core_class_name set

        Returns:
            Reloaded action class if successful, None otherwise
        """
        if not metadata.core_module_path or not metadata.core_class_name:
            logger.warning("Core action metadata missing module path or class name")
            return None

        try:
            # Check if module is already loaded
            if metadata.core_module_path in sys.modules:
                # Reload the module
                module = sys.modules[metadata.core_module_path]
                importlib.reload(module)
                logger.debug(f"Reloaded core action module: {metadata.core_module_path}")
            else:
                # Module not loaded, load it fresh
                module = importlib.import_module(metadata.core_module_path)
                logger.debug(f"Loaded core action module: {metadata.core_module_path}")

            # Get the action class from the reloaded module
            action_class = getattr(module, metadata.core_class_name, None)

            if action_class is None:
                logger.warning(
                    f"Class {metadata.core_class_name} not found in module {metadata.core_module_path}"
                )
                return None

            # Verify it's a subclass of Action
            if not issubclass(action_class, Action):
                logger.warning(
                    f"Class {metadata.core_class_name} is not a subclass of Action"
                )
                return None

            logger.debug(
                f"Reloaded core action class {metadata.core_class_name} from {metadata.core_module_path}"
            )
            return action_class

        except ImportError as e:
            logger.error(
                f"Error reloading core action module {metadata.core_module_path}: {e}",
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(
                f"Error reloading core action class {metadata.core_class_name}: {e}",
                exc_info=True,
            )
            return None

    def _unload_and_reload_action_module(
        self,
        module_name: str,
        action_dir: Path,
        action_name: str,
        archetype: str,
    ) -> Optional[Type[Action]]:
        """Unload and reload an action module to ensure fresh code is loaded.

        This method:
        1. Unloads the module from sys.modules if it exists
        2. Reloads the module fresh using _load_action_module()

        Args:
            module_name: Full module name (e.g., "jvagent.actions.namespace.action_name")
            action_dir: Directory containing the action
            action_name: Name of the action (for constructing file paths)
            archetype: Class name to load

        Returns:
            Reloaded action class if successfully loaded, None otherwise
        """
        # Unload module if it exists
        if module_name in sys.modules:
            try:
                del sys.modules[module_name]
                logger.debug(f"Unloaded module: {module_name}")
            except Exception as e:
                logger.warning(f"Error unloading module {module_name}: {e}")

        # Also unload parent packages if they're action-specific
        module_parts = module_name.split(".")
        if len(module_parts) > 3 and module_parts[0] == "jvagent" and module_parts[1] == "actions":
            # Unload parent packages (e.g., jvagent.actions.namespace)
            for i in range(3, len(module_parts) + 1):
                parent_module = ".".join(module_parts[:i])
                if parent_module in sys.modules:
                    try:
                        del sys.modules[parent_module]
                        logger.debug(f"Unloaded parent module: {parent_module}")
                    except Exception:
                        pass  # Ignore errors unloading parent packages

        # Reload the module fresh
        return self._load_action_module(module_name, action_dir, action_name, archetype)

    def _load_core_action_class(self, metadata: ActionMetadata) -> Optional[Type[Action]]:
        """Load action class from core jvagent library.

        This method ensures parent packages are imported so their __init__.py files
        (which import endpoints) are executed. This is critical for endpoint discovery
        on app restart.

        Args:
            metadata: Action metadata with core_module_path and core_class_name set

        Returns:
            Action class if successfully loaded, None otherwise
        """
        if not metadata.core_module_path or not metadata.core_class_name:
            logger.warning("Core action metadata missing module path or class name")
            return None

        try:
            # Import parent packages first to ensure their __init__.py files execute
            # This is critical for endpoint discovery - parent package __init__.py files
            # import endpoints.py modules which register endpoints via @endpoint decorators
            module_path_parts = metadata.core_module_path.split(".")
            
            # Import all parent packages (e.g., for "jvagent.action.model.language.openai",
            # import "jvagent", "jvagent.action", "jvagent.action.model", "jvagent.action.model.language")
            for i in range(2, len(module_path_parts)):
                parent_package = ".".join(module_path_parts[:i])
                try:
                    importlib.import_module(parent_package)
                except ImportError:
                    # Parent package import failed, but continue - the module import will fail if critical
                    pass

            # Import the module using the core module path
            # e.g., "jvagent.action.router.interact_router"
            # This will also trigger parent package imports if not already imported
            module = importlib.import_module(metadata.core_module_path)

            # Get the action class from the module
            action_class = getattr(module, metadata.core_class_name, None)

            if action_class is None:
                logger.warning(
                    f"Class {metadata.core_class_name} not found in module {metadata.core_module_path}"
                )
                return None

            # Verify it's a subclass of Action
            if not issubclass(action_class, Action):
                logger.warning(
                    f"Class {metadata.core_class_name} is not a subclass of Action"
                )
                return None

            logger.debug(
                f"Loaded core action class {metadata.core_class_name} from {metadata.core_module_path}"
            )
            return action_class

        except ImportError as e:
            logger.error(
                f"Error importing core action module {metadata.core_module_path}: {e}",
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(
                f"Error loading core action class {metadata.core_class_name}: {e}",
                exc_info=True,
            )
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
                        # Validate that the property exists on the action class or any inherited base class
                        # Check both:
                        # 1. hasattr() - for regular class attributes and descriptors
                        # 2. model_fields - for Pydantic fields (including @attribute decorated properties)
                        property_exists = False
                        
                        # Check hasattr first (covers descriptors, @property, regular attributes)
                        if hasattr(action_class, key):
                            property_exists = True
                        else:
                            # Check model_fields for Pydantic fields (including @attribute decorated)
                            # This is necessary because @attribute returns Field() which is stored in model_fields
                            for cls in action_class.__mro__:
                                if hasattr(cls, "model_fields") and key in cls.model_fields:
                                    property_exists = True
                                    break
                        
                        if not property_exists:
                            logger.warning(
                                f"Property '{key}' from agent.yaml context does not exist on "
                                f"{action_class.__name__} or any of its inherited base classes. "
                                f"Skipping override."
                            )
                            continue
                        action_data[key] = value

            # Create action instance with metadata
            action = action_class(**action_data)

            # Merge config: base config from info.yaml + overrides from agent.yaml
            merged_config = dict(metadata.config) if metadata.config else {}
            if config_overrides:
                merged_config.update(config_overrides)

            # Track loaded modules for this action
            # Store module paths that were loaded (for potential unloading during deregistration)
            loaded_modules = []
            if hasattr(metadata, "core_module_path") and metadata.core_module_path:
                # Core action: track the core module path and parent packages
                module_path_parts = metadata.core_module_path.split(".")
                for i in range(2, len(module_path_parts) + 1):
                    parent_module = ".".join(module_path_parts[:i])
                    if parent_module in sys.modules:
                        loaded_modules.append(parent_module)
            else:
                # Local action: track the action module and parent packages
                # Use agent-specific module path if agent info is available
                if metadata.agent_namespace and metadata.agent_name:
                    module_name = (
                        f"jvagent.actions.{metadata.agent_namespace}.{metadata.agent_name}."
                        f"{metadata.namespace}.{metadata.name}"
                    )
                else:
                    # Fallback to old format for backward compatibility
                    module_name = f"jvagent.actions.{metadata.namespace}.{metadata.name}"
                if module_name in sys.modules:
                    loaded_modules.append(module_name)
                # Also track parent packages
                module_parts = module_name.split(".")
                for i in range(2, len(module_parts) + 1):
                    parent_module = ".".join(module_parts[:i])
                    if parent_module in sys.modules and parent_module not in loaded_modules:
                        loaded_modules.append(parent_module)

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
                "loaded_modules": loaded_modules,  # Track modules for cleanup
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

        Actions are discovered in the following order:
        1. Local actions from filesystem (agents/{namespace}/{agent_name}/actions/)
        2. Core actions from jvagent library (if not found locally)
        3. Error if action not found in either location

        Args:
            namespace: Namespace of the agent (for filesystem path)
            agent_name: Name of the agent (for filesystem path)
            agent_id: ID of the agent node
            action_configs: Optional list of action configurations from agent.yaml

        Returns:
            List of action instances
        """
        # Discover available actions from local filesystem first
        discovered = self.discover_actions(namespace, agent_name)
        discovered_lookup = {f"{m.namespace}/{m.name}": m for m in discovered}

        # Build action config lookup (using namespace/action_name format)
        # Resolve environment variable placeholders in action configs
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

                action_namespace, action_name = action_ref.split("/", 1)
                key = f"{action_namespace}/{action_name}"
                config_lookup[key] = cfg

                # If action not found locally, try core discovery
                if key not in discovered_lookup:
                    core_metadata = self.discover_core_action(action_namespace, action_name)
                    if core_metadata:
                        discovered.append(core_metadata)
                        discovered_lookup[key] = core_metadata
                        logger.debug(f"Using core action for {key}")
                    else:
                        logger.warning(
                            f"Action {key} not found locally or in core library. Skipping."
                        )

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
