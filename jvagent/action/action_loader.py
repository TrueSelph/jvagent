import importlib
import importlib.abc
import importlib.util
import logging
import os
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union

import yaml

from jvagent.action.base import Action
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
        return f"ActionMetadata(namespace={self.namespace}, name={self.name}, version={self.version})"


class ActionRegistry:
    """Registry for tracking required, resolved, and imported actions.

    This class manages the lifecycle of action loading:
    - Required actions: Actions explicitly listed in agent.yaml
    - Resolved actions: Required actions + their transitive dependencies
    - Imported actions: Actions that have been successfully imported
    """

    def __init__(self):
        """Initialize the action registry."""
        self.required_actions: Set[str] = (
            set()
        )  # e.g., {"jvagent/whatsapp", "jvagent/interact_router"}
        self.resolved_actions: Set[str] = set()  # After dependency resolution
        self.imported_actions: Set[str] = set()  # Successfully imported
        self.action_metadata: Dict[str, ActionMetadata] = {}  # Cached metadata
        self._resolving: Set[str] = (
            set()
        )  # Track actions currently being resolved (prevents cycles)

    def add_required_action(self, action_ref: str) -> None:
        """Mark action as required (from agent.yaml).

        Args:
            action_ref: Action reference in "namespace/action_name" format
        """
        if action_ref and "/" in action_ref:
            self.required_actions.add(action_ref)

    def should_import_action(self, action_ref: str) -> bool:
        """Check if action should be imported.

        Args:
            action_ref: Action reference in "namespace/action_name" format

        Returns:
            True if action should be imported, False otherwise
        """
        return (
            action_ref in self.resolved_actions
            and action_ref not in self.imported_actions
        )

    def mark_imported(self, action_ref: str) -> None:
        """Mark action as successfully imported.

        Args:
            action_ref: Action reference in "namespace/action_name" format
        """
        self.imported_actions.add(action_ref)

    def is_resolving(self, action_ref: str) -> bool:
        """Check if action is currently being resolved (prevents circular dependencies).

        Args:
            action_ref: Action reference in "namespace/action_name" format

        Returns:
            True if action is currently being resolved
        """
        return action_ref in self._resolving

    def start_resolving(self, action_ref: str) -> None:
        """Mark action as being resolved.

        Args:
            action_ref: Action reference in "namespace/action_name" format
        """
        self._resolving.add(action_ref)

    def finish_resolving(self, action_ref: str) -> None:
        """Mark action as finished resolving.

        Args:
            action_ref: Action reference in "namespace/action_name" format
        """
        self._resolving.discard(action_ref)


# Module path prefix for app-loaded actions (custom actions in app's agents/ directory).
# Format: jvagent.actions.{agent_ns}.{agent_name}.{action_ns}.{action_name}
_ACTIONS_PREFIX = "jvagent.actions."

# Global base path for the importer, set by ActionLoader.__init__
_actions_importer_base_path: Optional[Path] = None


class JvagentActionsImporter(importlib.abc.MetaPathFinder):
    """Import hook that resolves jvagent.actions.* to the app directory's agents/ tree.

    Used when jvagent is installed as a pip package and the app directory (e.g. iris_ai)
    is the deployment target. Custom actions live under {base_path}/agents/ and are
    exposed as jvagent.actions.{agent_ns}.{agent_name}.{action_ns}.{action_name}.

    Supports lazy base_path via a callable for early registration.
    """

    def __init__(self, base_path: Union[Path, Callable[[], Optional[Path]]]):
        """Initialize the importer.

        Args:
            base_path: Either a Path or a callable returning Optional[Path].
                      When callable, find_spec will call it to get the current base_path.
        """
        self._base_path = base_path

    def find_spec(
        self,
        fullname: str,
        path: Optional[List[str]],
        target: Optional[types.ModuleType] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        if not fullname.startswith(_ACTIONS_PREFIX):
            return None

        # Resolve base_path (may be a callable for lazy initialization)
        base_path = self._base_path() if callable(self._base_path) else self._base_path
        if base_path is None:
            return None

        agents_path = base_path / "agents"
        if not agents_path.exists() or not agents_path.is_dir():
            return None

        rest = fullname[len(_ACTIONS_PREFIX) :]

        # Handle exact "jvagent.actions" (rest is empty string)
        if rest == "":
            spec = importlib.machinery.ModuleSpec(
                fullname, loader=None, is_package=True
            )
            spec.submodule_search_locations = [str(agents_path)]
            return spec

        parts = rest.split(".")
        if len(parts) < 1:
            return None

        # Map module hierarchy to filesystem (see plan path mapping).
        # parts[0]=agent_ns, parts[1]=agent_name, parts[2]=action_ns, parts[3]=action_name, ...
        if len(parts) == 1:
            # jvagent.actions.{agent_ns}
            dir_path = agents_path / parts[0]
        elif len(parts) == 2:
            # jvagent.actions.{agent_ns}.{agent_name}
            dir_path = agents_path / parts[0] / parts[1] / "actions"
        elif len(parts) == 3:
            # jvagent.actions.{agent_ns}.{agent_name}.{action_ns}
            dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
        elif len(parts) == 4:
            # jvagent.actions.{agent_ns}.{agent_name}.{action_ns}.{action_name} (action package)
            dir_path = (
                agents_path / parts[0] / parts[1] / "actions" / parts[2] / parts[3]
            )
        else:
            # jvagent.actions....{action_name}.{submodule} -> e.g. .endpoints, .prompts
            action_dir = (
                agents_path / parts[0] / parts[1] / "actions" / parts[2] / parts[3]
            )
            submodule = ".".join(parts[4:])
            module_file = action_dir / f"{parts[4]}.py"
            if len(parts) == 5 and module_file.exists():
                spec = importlib.util.spec_from_file_location(
                    fullname, module_file, submodule_search_locations=[str(action_dir)]
                )
                return spec
            # Deeper submodule (e.g. utils.helpers)
            subpath = action_dir / parts[4]
            if len(parts) == 5:
                init = subpath / "__init__.py"
                if subpath.is_dir() and init.exists():
                    spec = importlib.util.spec_from_file_location(
                        fullname, init, submodule_search_locations=[str(subpath)]
                    )
                    return spec
                if module_file.exists():
                    spec = importlib.util.spec_from_file_location(
                        fullname,
                        module_file,
                        submodule_search_locations=[str(action_dir)],
                    )
                    return spec
            else:
                # parts[4] is a package, look for parts[5].py or parts[5]/__init__.py
                mid = action_dir
                for i in range(4, len(parts) - 1):
                    mid = mid / parts[i]
                last = parts[-1]
                file_py = mid / f"{last}.py"
                dir_init = mid / last / "__init__.py"
                if file_py.exists():
                    spec = importlib.util.spec_from_file_location(
                        fullname, file_py, submodule_search_locations=[str(mid)]
                    )
                    return spec
                if dir_init.exists():
                    spec = importlib.util.spec_from_file_location(
                        fullname,
                        dir_init,
                        submodule_search_locations=[str(mid / last)],
                    )
                    return spec
            return None

        if not dir_path.exists() or not dir_path.is_dir():
            return None

        # Namespace package (2–4 parts) or action package (5 parts = 4 parts in rest)
        if len(parts) <= 3:
            spec = importlib.machinery.ModuleSpec(
                fullname, loader=None, is_package=True
            )
            spec.submodule_search_locations = [str(dir_path)]
            return spec

        # len(parts) == 4: action package directory
        init_file = dir_path / "__init__.py"
        module_file = dir_path / f"{parts[3]}.py"
        if init_file.exists():
            spec = importlib.util.spec_from_file_location(
                fullname,
                init_file,
                submodule_search_locations=[str(dir_path)],
            )
            return spec
        if module_file.exists():
            spec = importlib.util.spec_from_file_location(
                fullname, module_file, submodule_search_locations=[str(dir_path)]
            )
            return spec
        return None


# Global importer instance registered at module load time
_actions_importer = JvagentActionsImporter(lambda: _actions_importer_base_path)


class ActionLoader:
    """Loader for discovering and instantiating actions from the filesystem."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize the action loader.

        Args:
            base_path: Base path to search for actions. If None, uses current directory.
        """
        global _actions_importer_base_path

        self.base_path = Path(base_path or os.getcwd())
        self._core_action_path: Optional[Path] = None
        self._core_action_cache: Optional[Dict[str, Dict[str, Any]]] = None

        # Set the global base path for the importer (registered at module load time)
        _actions_importer_base_path = self.base_path

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
            from jvagent.core.dependency_installer import install_action_dependencies

            install_action_dependencies(data, action_name, action_dir)
        except Exception as e:
            logger.warning(f"Error installing dependencies for {action_name}: {e}")

    def _ensure_action_parent_packages(
        self, module_name: str, action_dir: Path
    ) -> None:
        """Ensure parent packages exist in sys.modules with correct __path__ for relative imports.

        Does not rely on JvagentActionsImporter (which may fail in Lambda). Creates namespace
        packages manually using the same path mapping as the finder.

        Args:
            module_name: Full module name (e.g., "jvagent.actions.jvagent.iris_ai.jvagent.news_interact_action")
            action_dir: Directory containing the action (used to derive agents_path)
        """
        if not module_name.startswith(_ACTIONS_PREFIX):
            return
        rest = module_name[len(_ACTIONS_PREFIX) :]
        parts = rest.split(".")
        if len(parts) < 1:
            return
        # Derive agents_path: action_dir is .../agents/{agent_ns}/{agent_name}/actions/{action_ns}/{action_name}
        # Walk up: action_name -> action_ns -> actions -> agent_name -> agent_ns -> agents
        agents_path = action_dir
        for _ in range(5):
            agents_path = agents_path.parent
        if agents_path.name != "agents":
            logger.debug(
                f"Expected 'agents' when walking up from {action_dir}, got {agents_path.name}"
            )
            return
        # jvagent.actions (0 parts)
        if "jvagent.actions" not in sys.modules:
            mod = types.ModuleType("jvagent.actions")
            mod.__path__ = [str(agents_path)]
            mod.__package__ = "jvagent"
            sys.modules["jvagent.actions"] = mod
        # Parents 1..len(parts)-1 (we don't create the full module_name - that's the action we load)
        for i in range(1, len(parts)):
            parent_name = _ACTIONS_PREFIX + ".".join(parts[:i])
            if parent_name in sys.modules:
                continue
            if i == 1:
                dir_path = agents_path / parts[0]
            elif i == 2:
                dir_path = agents_path / parts[0] / parts[1] / "actions"
            elif i == 3:
                dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
            else:
                # Deeper hierarchy (e.g. subpackages under action)
                dir_path = agents_path / parts[0] / parts[1] / "actions" / parts[2]
                for j in range(3, i):
                    dir_path = dir_path / parts[j]
            if not dir_path.exists() or not dir_path.is_dir():
                continue
            mod = types.ModuleType(parent_name)
            mod.__path__ = [str(dir_path)]
            mod.__package__ = (
                "jvagent.actions"
                if i == 1
                else _ACTIONS_PREFIX + ".".join(parts[: i - 1])
            )
            sys.modules[parent_name] = mod

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
        # Ensure parent packages exist in sys.modules (does not rely on MetaPathFinder)
        self._ensure_action_parent_packages(module_name, action_dir)

        # Avoid re-loading: module may already be loaded by pre_import_action_modules_for_agents.
        # Re-execution would re-run @endpoint decorators and cause duplicate route registration.
        if module_name in sys.modules:
            existing = sys.modules[module_name]
            action_class = getattr(existing, archetype, None)
            if action_class is not None and issubclass(action_class, Action):
                return action_class

        init_file = action_dir / "__init__.py"
        module_file = action_dir / f"{action_name}.py"

        # Try loading as package first (if __init__.py exists)
        # This ensures __init__.py executes, which imports endpoints
        if init_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    module_name, init_file, submodule_search_locations=[str(action_dir)]
                )

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
                                    f"{module_name}.{action_name}",
                                    module_file,
                                    submodule_search_locations=[str(action_dir)],
                                )
                                if module_spec and module_spec.loader:
                                    module = importlib.util.module_from_spec(
                                        module_spec
                                    )
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
            spec = importlib.util.spec_from_file_location(
                module_name, module_file, submodule_search_locations=[str(action_dir)]
            )

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
            logger.error(
                f"Error loading action class from {module_file}: {e}", exc_info=True
            )
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
            if "__pycache__" in info_file.parts or any(
                part.startswith("_") for part in info_file.parts[:-1]
            ):
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

    def _load_action_metadata_for_deps(
        self, action_ref: str, core_cache: Dict[str, Dict[str, Any]]
    ) -> Optional[ActionMetadata]:
        """Load action metadata for dependency resolution.

        This method loads metadata from info.yaml files for both core and local actions.
        It's used during dependency resolution to discover action dependencies.

        Args:
            action_ref: Action reference in "namespace/action_name" format
            core_cache: Core action cache from _build_core_action_cache()

        Returns:
            ActionMetadata if found, None otherwise
        """
        if "/" not in action_ref:
            return None

        namespace, action_name = action_ref.split("/", 1)

        # Try core action first
        if namespace == "jvagent":
            action_info = core_cache.get(action_name)
            if action_info:
                action_dir = action_info["dir"]
                data = action_info.get("data")
                if not data:
                    # Fallback: load from file
                    info_file = action_dir / "info.yaml"
                    if info_file.exists():
                        data = self._load_info_yaml(info_file)
                if data:
                    metadata = ActionMetadata(data, action_dir, namespace=namespace)
                    metadata.is_core_action = True
                    return metadata

        # Try local action (from filesystem)
        agents_path = self.base_path / "agents"
        if agents_path.exists() and agents_path.is_dir():
            # Search through all agent action directories
            for agent_namespace_dir in agents_path.iterdir():
                if not agent_namespace_dir.is_dir():
                    continue
                for agent_dir in agent_namespace_dir.iterdir():
                    if not agent_dir.is_dir():
                        continue
                    agent_actions_path = agent_dir / "actions"
                    if not agent_actions_path.exists():
                        continue
                    for action_namespace_dir in agent_actions_path.iterdir():
                        if (
                            not action_namespace_dir.is_dir()
                            or action_namespace_dir.name != namespace
                        ):
                            continue
                        for action_dir in action_namespace_dir.iterdir():
                            if not action_dir.is_dir():
                                continue
                            info_file = action_dir / "info.yaml"
                            if not info_file.exists():
                                continue
                            data = self._load_info_yaml(info_file)
                            if not data:
                                continue
                            package = data.get("package", {})
                            if not isinstance(package, dict):
                                continue
                            extracted_name = self._extract_action_name(
                                package, action_dir
                            )
                            if extracted_name == action_name:
                                return ActionMetadata(
                                    data, action_dir, namespace=namespace
                                )

        return None

    def _resolve_action_dependencies(
        self,
        action_ref: str,
        core_cache: Dict[str, Dict[str, Any]],
        registry: ActionRegistry,
    ) -> Set[str]:
        """Recursively resolve action dependencies.

        This method resolves transitive dependencies by:
        1. Loading the action's info.yaml
        2. Extracting dependencies from dependencies.actions
        3. Recursively resolving each dependency
        4. Tracking visited actions to prevent circular dependencies

        Args:
            action_ref: Action reference in "namespace/action_name" format
            core_cache: Core action cache from _build_core_action_cache()
            registry: ActionRegistry instance to track resolution state

        Returns:
            Set of all action refs needed (including transitive deps)
        """
        # Prevent circular dependencies
        if registry.is_resolving(action_ref):
            logger.debug(f"Circular dependency detected for {action_ref}, skipping")
            return {action_ref}  # Return self to include in resolved set

        if action_ref in registry.resolved_actions:
            # Already resolved, return empty set (already counted)
            return set()

        registry.start_resolving(action_ref)
        all_deps = {action_ref}

        try:
            # Load metadata to get dependencies
            metadata = self._load_action_metadata_for_deps(action_ref, core_cache)
            if not metadata:
                logger.debug(
                    f"Could not load metadata for {action_ref}, skipping dependency resolution"
                )
                registry.resolved_actions.add(action_ref)
                return all_deps

            # Get action dependencies from info.yaml
            dependencies = metadata.dependencies.get("actions", [])
            if not isinstance(dependencies, list):
                dependencies = []

            # Recursively resolve each dependency
            for dep_ref in dependencies:
                if not isinstance(dep_ref, str) or "/" not in dep_ref:
                    logger.warning(
                        f"Invalid dependency format in {action_ref}: {dep_ref}"
                    )
                    continue

                dep_set = self._resolve_action_dependencies(
                    dep_ref, core_cache, registry
                )
                all_deps.update(dep_set)

            # Mark as resolved
            registry.resolved_actions.add(action_ref)
            registry.action_metadata[action_ref] = metadata

        except Exception as e:
            logger.warning(f"Error resolving dependencies for {action_ref}: {e}")
            # Still mark as resolved to avoid infinite loops
            registry.resolved_actions.add(action_ref)
        finally:
            registry.finish_resolving(action_ref)

        return all_deps

    def discover_core_action(
        self, namespace: str, action_name: str
    ) -> Optional[ActionMetadata]:
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
            metadata.core_module_path = (
                f"jvagent.action.{category_module}.{module_file}"
            )
        metadata.core_class_name = class_name

        logger.debug(
            f"Discovered core action: {namespace}/{action_name} from {action_dir}"
        )
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

        Note: This method imports ALL core actions. For conditional loading based on
        agent.yaml configuration, use pre_import_action_modules_for_agents() instead.
        """
        imported_count = 0

        # First, scan all agent.yaml files to find required core actions
        agents_path = self.base_path / "agents"
        agent_paths = []
        if agents_path.exists() and agents_path.is_dir():
            for namespace_dir in agents_path.iterdir():
                if not namespace_dir.is_dir():
                    continue
                namespace = namespace_dir.name
                for agent_dir in namespace_dir.iterdir():
                    if not agent_dir.is_dir():
                        continue
                    agent_name = agent_dir.name
                    agent_paths.append((namespace, agent_name))

        # Scan agent.yaml files to find required core actions
        required_core_actions = (
            self._scan_required_core_actions(agent_paths) if agent_paths else None
        )

        # Pre-import core action packages (only required ones if agent_paths found)
        core_imported = self._pre_import_core_action_packages(
            required_actions=required_core_actions
        )
        imported_count += core_imported

        # Then, pre-import local actions from filesystem
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            logger.debug(f"Agents directory not found: {agents_path}")
            if imported_count > 0:
                logger.debug(
                    f"Pre-imported {imported_count} action class(es) for class discovery"
                )
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
                            self._ensure_dependencies_installed(
                                data, action_name, action_dir
                            )

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
                            logger.warning(
                                f"Error pre-importing action from {action_dir}: {e}"
                            )
                            continue

        if imported_count > 0:
            logger.debug(
                f"Pre-imported {imported_count} action class(es) for class discovery"
            )

    def pre_import_action_modules_for_agents(self, agent_refs: List[str]) -> None:
        """Pre-import action modules only for specified agents from app.yaml.

        This method implements conditional loading:
        1. Scans agent.yaml files to collect required actions
        2. Resolves transitive dependencies from info.yaml
        3. Only imports modules for resolved actions
        4. Ensures endpoints are registered only for loaded actions

        This ensures that only agents listed in app.yaml have their modules loaded,
        preventing conflicts and ensuring proper module isolation between agents.

        Args:
            agent_refs: List of agent references in "namespace/agent_name" format
        """
        # Build set of agent paths for quick lookup
        agent_paths = []
        for agent_ref in agent_refs:
            if "/" not in agent_ref:
                logger.warning(
                    f"Invalid agent reference format (expected 'namespace/agent_name'): {agent_ref}"
                )
                continue
            namespace, agent_name = agent_ref.split("/", 1)
            agent_paths.append((namespace, agent_name))

        if not agent_paths:
            logger.debug("No valid agent paths found")
            return

        # Step 1: Build action registry
        registry = ActionRegistry()

        # Step 2: Scan agent.yaml files to populate required actions
        required_actions = self._scan_required_actions(agent_paths)
        for action_ref in required_actions:
            registry.add_required_action(action_ref)

        if not registry.required_actions:
            logger.debug("No actions found in agent.yaml files")
            return

        logger.debug(
            f"Found {len(registry.required_actions)} required action(s) from agent.yaml"
        )

        # Step 3: Resolve dependencies transitively
        core_cache = self._build_core_action_cache()
        for action_ref in list(registry.required_actions):
            try:
                resolved = self._resolve_action_dependencies(
                    action_ref, core_cache, registry
                )
                logger.debug(
                    f"Resolved {len(resolved)} action(s) for {action_ref} "
                    f"(including {len(resolved) - 1} dependencies)"
                )
            except Exception as e:
                logger.warning(f"Error resolving dependencies for {action_ref}: {e}")

        logger.debug(
            f"Resolved {len(registry.resolved_actions)} total action(s) "
            f"({len(registry.required_actions)} required + {len(registry.resolved_actions) - len(registry.required_actions)} dependencies)"
        )

        # Step 4: Import only resolved core actions
        imported_count = 0
        for action_ref in registry.resolved_actions:
            if action_ref.startswith("jvagent/"):
                # Core action - import conditionally
                if self._import_action_module_conditionally(action_ref, registry):
                    imported_count += 1
            else:
                # Local action - will be imported during agent loading
                # We still mark it as "should import" for tracking
                logger.debug(
                    f"Local action {action_ref} will be imported during agent loading"
                )

        # Step 5: Pre-import local actions only for specified agents
        agent_paths_set = set(agent_paths)
        agents_path = self.base_path / "agents"

        if agents_path.exists() and agents_path.is_dir():
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
                    if (agent_namespace, agent_name) not in agent_paths_set:
                        continue

                    # Look for actions directory
                    agent_actions_path = agent_dir / "actions"
                    if (
                        not agent_actions_path.exists()
                        or not agent_actions_path.is_dir()
                    ):
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
                                action_name = self._extract_action_name(
                                    package, action_dir
                                )
                                action_namespace = action_namespace_dir.name
                                action_ref = f"{action_namespace}/{action_name}"

                                # Only import if in resolved actions
                                if action_ref not in registry.resolved_actions:
                                    logger.debug(
                                        f"Skipping local action {action_ref} (not in resolved actions)"
                                    )
                                    continue

                                # Skip if already imported
                                if action_ref in registry.imported_actions:
                                    continue

                                archetype = package.get("archetype", "Action")

                                # Install pip dependencies before importing
                                self._ensure_dependencies_installed(
                                    data, action_name, action_dir
                                )

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
                                    registry.mark_imported(action_ref)
                                    imported_count += 1

                            except Exception as e:
                                logger.warning(
                                    f"Error pre-importing action from {action_dir}: {e}"
                                )
                                continue

        if imported_count > 0:
            logger.debug(
                f"Pre-imported {imported_count} action class(es) for class discovery "
                f"({len([a for a in registry.imported_actions if a.startswith('jvagent/')])} core, "
                f"{len([a for a in registry.imported_actions if not a.startswith('jvagent/')])} local)"
            )

    def _scan_required_actions(self, agent_paths: List[Tuple[str, str]]) -> Set[str]:
        """Scan agent.yaml files to find all configured actions (core + local).

        Args:
            agent_paths: List of (namespace, agent_name) tuples to scan

        Returns:
            Set of all action references in "namespace/action_name" format
            (e.g., "jvagent/interact_router", "contrib/custom_action")
        """
        required_actions = set()
        agents_path = self.base_path / "agents"

        if not agents_path.exists() or not agents_path.is_dir():
            return required_actions

        for namespace, agent_name in agent_paths:
            agent_path = agents_path / namespace / agent_name
            agent_file = agent_path / "agent.yaml"

            if not agent_file.exists():
                continue

            try:
                with open(agent_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if not data:
                    continue

                # Resolve environment variable placeholders
                data = resolve_env_placeholders(data)

                # Extract action references from actions list
                actions = data.get("actions", [])
                for action_config in actions:
                    if not isinstance(action_config, dict):
                        continue
                    action_ref = action_config.get("action", "")
                    if action_ref and "/" in action_ref:
                        # Include all actions (core + local)
                        required_actions.add(action_ref)

            except Exception as e:
                logger.warning(
                    f"Error scanning agent.yaml for {namespace}/{agent_name}: {e}"
                )
                continue

        return required_actions

    def _scan_required_core_actions(
        self, agent_paths: List[Tuple[str, str]]
    ) -> Set[str]:
        """Scan agent.yaml files to find core actions that are actually configured.

        This method is kept for backward compatibility. New code should use
        _scan_required_actions() which returns all actions.

        Args:
            agent_paths: List of (namespace, agent_name) tuples to scan

        Returns:
            Set of core action references in "namespace/action_name" format (e.g., "jvagent/interact_router")
        """
        all_actions = self._scan_required_actions(agent_paths)
        # Filter to only core actions for backward compatibility
        return {
            action_ref
            for action_ref in all_actions
            if action_ref.startswith("jvagent/")
        }

    def _pre_import_core_action_packages(
        self, required_actions: Optional[Set[str]] = None
    ) -> int:
        """Pre-import core action packages to ensure their __init__.py files execute.

        This is critical for endpoint discovery - parent package __init__.py files
        import endpoints.py modules which register endpoints via @endpoint decorators.

        Also installs pip dependencies for core actions before importing.

        Args:
            required_actions: Optional set of core action references in "namespace/action_name" format.
                           If provided, only actions in this set will be imported.
                           If None, imports all core actions (backward compatibility).

        Returns:
            Number of core action packages imported
        """
        core_path = self._get_core_action_path()
        if not core_path:
            return 0

        imported_count = 0

        # Build core action cache to get all core actions
        action_cache = self._build_core_action_cache()

        # Filter actions if required_actions is provided
        actions_to_import = {}
        if required_actions is not None:
            # Only import actions that are in the required set
            for action_name, action_info in action_cache.items():
                action_ref = f"jvagent/{action_name}"
                if action_ref in required_actions:
                    actions_to_import[action_name] = action_info
        else:
            # Import all core actions (backward compatibility)
            actions_to_import = action_cache

        if not actions_to_import:
            logger.debug("No core actions to import (filtered or none available)")
            return 0

        # First, install dependencies for actions we'll import
        for action_name, action_info in actions_to_import.items():
            action_dir = action_info["dir"]
            data = action_info.get("data")

            if data:
                # Install pip dependencies before importing
                self._ensure_dependencies_installed(data, action_name, action_dir)

        # Then import each core action's parent packages
        # We need to import parent packages, not just the action modules themselves
        # This ensures __init__.py files execute and import endpoints
        imported_packages = set()

        for action_name, action_info in actions_to_import.items():
            relative_path = action_info["relative_path"]

            # Build the full module path for the parent package
            # e.g., "model/language/openai" -> "jvagent.action.model.language"
            # We import the parent package, not the module itself
            path_parts = relative_path.split("/")

            # Import parent packages (skip the last part which is the module name)
            for i in range(len(path_parts)):
                parent_path = "/".join(path_parts[: i + 1])
                package_path = f"jvagent.action.{parent_path.replace('/', '.')}"

                # Only import each package once
                if package_path not in imported_packages:
                    try:
                        importlib.import_module(package_path)
                        imported_packages.add(package_path)
                        imported_count += 1
                        logger.debug(
                            f"Pre-imported core action package: {package_path}"
                        )
                    except ImportError as e:
                        # Some packages might not have __init__.py, that's okay
                        logger.debug(
                            f"Could not import core package {package_path}: {e}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error importing core package {package_path}: {e}"
                        )

        if imported_count > 0:
            logger.debug(
                f"Pre-imported {imported_count} core action package(s) for endpoint discovery"
            )

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
        actions_path = (
            self.base_path / "agents" / agent_namespace / agent_name / "actions"
        )

        if not actions_path.exists() or not actions_path.is_dir():
            return []

        discovered = []
        logger.debug(f"Discovering actions from: {actions_path}")

        # Iterate through namespace directories in the actions folder
        for namespace_dir in actions_path.iterdir():
            if not namespace_dir.is_dir():
                continue

            action_namespace = (
                namespace_dir.name
            )  # Action namespace (different from agent namespace)

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
                    metadata = ActionMetadata(
                        data, action_dir, namespace=action_namespace
                    )
                    # Store agent information for agent-specific module paths
                    metadata.agent_namespace = agent_namespace
                    metadata.agent_name = agent_name
                    discovered.append(metadata)

                except Exception as e:
                    # Log error but continue discovering other actions
                    logger.warning(
                        f"Error loading action metadata from {info_file}: {e}"
                    )
                    continue

        return discovered

    def _import_action_module_conditionally(
        self,
        action_ref: str,
        registry: ActionRegistry,
    ) -> bool:
        """Import action module and its endpoints only if needed.

        This triggers:
        - Module import (action_name.py or __init__.py)
        - Endpoint import (via __init__.py: from . import endpoints)
        - Endpoint registration (via @endpoint decorator to deferred registry)

        Args:
            action_ref: Action reference in "namespace/action_name" format
            registry: ActionRegistry instance

        Returns:
            True if successfully imported, False otherwise
        """
        if "/" not in action_ref:
            return False

        namespace, action_name = action_ref.split("/", 1)

        if namespace == "jvagent":
            # Core action
            return self._import_core_action_module_conditionally(action_name, registry)
        else:
            # Local action - handled during agent-specific pre-import
            # This method is primarily for core actions
            logger.debug(
                f"Skipping local action import in conditional import: {action_ref}"
            )
            return False

    def _import_core_action_module_conditionally(
        self,
        action_name: str,
        registry: ActionRegistry,
    ) -> bool:
        """Import core action module conditionally.

        Args:
            action_name: Core action name (e.g., "whatsapp", "interact_router")
            registry: ActionRegistry instance

        Returns:
            True if successfully imported, False otherwise
        """
        action_ref = f"jvagent/{action_name}"

        # Check if already imported
        if action_ref in registry.imported_actions:
            return True

        # Check if should be imported
        if not registry.should_import_action(action_ref):
            logger.debug(f"Skipping import of {action_ref} (not in resolved actions)")
            return False

        try:
            # Get core action cache
            core_cache = self._build_core_action_cache()
            action_info = core_cache.get(action_name)

            if not action_info:
                logger.debug(f"Core action {action_name} not found in cache")
                return False

            action_dir = action_info["dir"]
            relative_path = action_info["relative_path"]
            data = action_info.get("data")

            # Install pip dependencies before importing
            if data:
                self._ensure_dependencies_installed(data, action_name, action_dir)

            # Import parent packages to ensure __init__.py files execute
            # This is critical for endpoint discovery
            path_parts = relative_path.split("/")
            imported_packages = set()

            # Import all parent packages (e.g., for "whatsapp" -> "jvagent.action.whatsapp")
            for i in range(len(path_parts)):
                parent_path = "/".join(path_parts[: i + 1])
                package_path = f"jvagent.action.{parent_path.replace('/', '.')}"

                # Only import each package once
                if package_path not in imported_packages:
                    try:
                        importlib.import_module(package_path)
                        imported_packages.add(package_path)
                        logger.debug(f"Imported core action package: {package_path}")
                    except ImportError as e:
                        # Some packages might not have __init__.py, that's okay
                        logger.debug(
                            f"Could not import core package {package_path}: {e}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error importing core package {package_path}: {e}"
                        )

            # Mark as imported
            registry.mark_imported(action_ref)
            logger.debug(f"Successfully imported core action module: {action_ref}")
            return True

        except Exception as e:
            logger.warning(f"Error importing core action module {action_ref}: {e}")
            return False

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

    def _reload_core_action_class(
        self, metadata: ActionMetadata
    ) -> Optional[Type[Action]]:
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
                logger.debug(
                    f"Reloaded core action module: {metadata.core_module_path}"
                )
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
        if (
            len(module_parts) > 3
            and module_parts[0] == "jvagent"
            and module_parts[1] == "actions"
        ):
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

    def _load_core_action_class(
        self, metadata: ActionMetadata
    ) -> Optional[Type[Action]]:
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
                    if not key.startswith("_") and key not in [
                        "id",
                        "agent_id",
                        "namespace",
                    ]:
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
                                if (
                                    hasattr(cls, "model_fields")
                                    and key in cls.model_fields
                                ):
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
                    module_name = (
                        f"jvagent.actions.{metadata.namespace}.{metadata.name}"
                    )
                if module_name in sys.modules:
                    loaded_modules.append(module_name)
                # Also track parent packages
                module_parts = module_name.split(".")
                for i in range(2, len(module_parts) + 1):
                    parent_module = ".".join(module_parts[:i])
                    if (
                        parent_module in sys.modules
                        and parent_module not in loaded_modules
                    ):
                        loaded_modules.append(parent_module)

            # Store metadata (including agent_name for path construction)
            action.metadata = {
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
            logger.error(
                f"Error creating action instance for {metadata.name}: {e}",
                exc_info=True,
            )
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
                    core_metadata = self.discover_core_action(
                        action_namespace, action_name
                    )
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
        seen_singleton_types: Set[str] = set()
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
                # Merge config into property_overrides for attributes that exist on actions.
                # This ensures permissions, action_aliases, etc. are applied directly to
                # class attributes (avoids config-in-metadata approach which fails on Lambda).
                for key in (
                    "permissions",
                    "action_aliases",
                    "default_deny",
                    "user_groups",
                    "exceptions",
                ):
                    if key in config_overrides:
                        property_overrides[key] = config_overrides[key]

            # Singleton filter: skip duplicate singleton types (first wins)
            is_singleton = metadata.config.get("singleton", True)
            if is_singleton and metadata.archetype in seen_singleton_types:
                continue
            if is_singleton:
                seen_singleton_types.add(metadata.archetype)

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


# Register the global importer at module load time so it's in sys.meta_path
# before any jvagent.actions.* imports occur (even if ActionLoader not yet created).
# The importer uses _actions_importer_base_path which is set when ActionLoader.__init__ runs.
if _actions_importer not in sys.meta_path:
    sys.meta_path.insert(0, _actions_importer)
