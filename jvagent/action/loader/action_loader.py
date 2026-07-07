import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Type

import yaml

from jvagent.action.base import Action
from jvagent.core.agent_yaml_validator import warn_agent_yaml
from jvagent.core.env_resolver import resolve_env_placeholders

from . import core_discovery, factory
from . import importer as _importer_module
from . import info_yaml, module_loading
from .metadata import ActionMetadata, ActionRegistry

_ACTIONS_PREFIX = _importer_module._ACTIONS_PREFIX

logger = logging.getLogger(__name__)


def _warn_if_anchorless_routable_ia(
    action: Action, metadata: ActionMetadata, agent_name: str
) -> None:
    """Emit a bootstrap WARNING for anchorless routable-candidate IAs.

    An "anchorless routable-candidate" IA is an :class:`InteractAction` that:

    - is not a pattern orchestrator (``manifest.pattern_orchestrator``)
    - is not always-execute (``always_execute=True``)
    - is anchor-routable (``manifest.routable_by_anchor`` default ``True``)
    - declares zero anchors via :attr:`anchors` or :meth:`get_anchors`

    Such an IA is invisible to first-entry routing — the Orchestrator only
    surfaces its tool when the utterance is anchor-relevant or the flow is
    already active. The warning surfaces the misconfiguration at install time.
    """
    try:
        from jvagent.action.interact.base import InteractAction
    except Exception:  # pragma: no cover — defensive
        return
    if not isinstance(action, InteractAction):
        return
    try:
        manifest = action.get_manifest()
    except Exception:
        return
    if manifest.pattern_orchestrator:
        return
    if getattr(action, "always_execute", False):
        return
    if not manifest.routable_by_anchor:
        return
    static_anchors = [
        a
        for a in (getattr(action, "anchors", None) or [])
        if isinstance(a, str) and a.strip()
    ]
    if static_anchors:
        return
    logger.warning(
        "agent.yaml: IA '%s/%s' on agent '%s' has no anchors declared and "
        "routable_by_anchor is not false; it will be hard to discover on "
        "first entry. Add anchors (or manifest activates_on) to make it "
        "routable, or set manifest.routable_by_anchor: false to mark it "
        "chain-internal.",
        metadata.namespace,
        metadata.name,
        agent_name,
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

        _importer_module._actions_importer_base_path = self.base_path

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _has_info_yaml_files(self, path: Path) -> bool:
        return info_yaml.has_info_yaml_files(path)

    def _load_info_yaml(self, info_file: Path) -> Optional[Dict[str, Any]]:
        return info_yaml.load_info_yaml(info_file)

    def _extract_action_name(self, package: Dict[str, Any], action_dir: Path) -> str:
        return info_yaml.extract_action_name(package, action_dir)

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

            install_action_dependencies(data, action_name)
        except Exception as e:
            logger.warning(f"Error installing dependencies for {action_name}: {e}")

    def _load_action_module(
        self,
        module_name: str,
        action_dir: Path,
        action_name: str,
        archetype: str,
    ) -> Optional[Type[Action]]:
        return module_loading.load_action_module(
            module_name, action_dir, action_name, archetype, _ACTIONS_PREFIX
        )

    # ============================================================================
    # Core Action Path and Discovery
    # ============================================================================

    def _get_core_action_path(self) -> Optional[Path]:
        return core_discovery.get_core_action_path(self)

    def _build_core_action_cache(self) -> Dict[str, Dict[str, Any]]:
        return core_discovery.build_core_action_cache(self)

    def invalidate_core_cache(self) -> None:
        """Forget the resolved core-action path and discovery cache.

        Call before re-running discovery when the core action layout may have
        changed (e.g. a new agent installation that brings new packages on
        sys.path).
        """
        self._core_action_path = None
        self._core_action_cache = None

    def expected_import_paths_from_descriptor_actions(
        self, actions_configs: Optional[List[Any]]
    ) -> Set[str]:
        """Build set of canonical import module paths for actions listed in agent.yaml.

        Used with persisted ``Action.module_path`` for fast staleness detection
        (set-diff vs filesystem) during reconcile.
        """
        paths: Set[str] = set()
        if not actions_configs:
            return paths
        core_cache = self._build_core_action_cache()
        for action_config in actions_configs:
            if not isinstance(action_config, dict):
                continue
            ref = action_config.get("action", "")
            if "/" not in ref:
                continue
            namespace, action_name = ref.split("/", 1)
            if namespace == "jvagent":
                info = core_cache.get(action_name)
                if not info:
                    continue
                action_dir = info["dir"]
                module_file = info["module_file"]
                category_module = info["relative_path"].replace("/", ".")
                init_file = action_dir / "__init__.py"
                if init_file.exists():
                    paths.add(f"jvagent.action.{category_module}")
                else:
                    paths.add(f"jvagent.action.{category_module}.{module_file}")
                continue
            meta = self._load_action_metadata_for_deps(ref, core_cache)
            if meta and getattr(meta, "module", None):
                paths.add(str(meta.module).strip())
        return paths

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
        required_actions: Set[str] = set()
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
                warn_agent_yaml(data, source=str(agent_file))

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
        - Endpoint registration (via @endpoint decorator; jvspatial sync_endpoint_modules
          handles uvicorn reload)

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

            # Trigger lazy class loading via __getattr__ so the action class
            # enters Python's __subclasses__() chain for jvspatial discovery.
            archetype = data.get("package", {}).get("archetype") if data else None
            if archetype:
                try:
                    pkg = sys.modules.get(package_path)
                    if pkg is not None:
                        getattr(pkg, archetype)  # fires __getattr__ → imports module
                except Exception:
                    pass

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

        if not metadata.agent_namespace or not metadata.agent_name:
            logger.error(
                "Cannot load action %s: metadata missing agent_namespace/agent_name",
                metadata.name,
            )
            return None

        module_name = (
            f"jvagent.actions.{metadata.agent_namespace}.{metadata.agent_name}."
            f"{metadata.namespace}.{metadata.name}"
        )
        return self._load_action_module(
            module_name, metadata.path, metadata.name, metadata.class_name
        )

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
            # import endpoints.py modules which register via @endpoint; sync_endpoint_modules
            # ensures registration at app build (including uvicorn reload)
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
        agent_namespace: str = "",
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
                            # Wave-3 review item: this warning ALREADY fired
                            # before May 2026 but only named the action class,
                            # not the agent or action ref — making it hard to
                            # pinpoint WHICH agent.yaml needed editing when an
                            # app loaded a dozen actions. The enhanced message
                            # below includes:
                            #   - agent name (so multi-agent apps can be
                            #     triaged)
                            #   - action namespace/label (so the operator
                            #     knows which YAML entry to edit)
                            #   - action class (kept — still the canonical
                            #     identifier for the receiving type)
                            # Kept at WARNING level so legacy YAMLs that
                            # carry inert keys don't fail to boot — only the
                            # offending field is dropped, the action still
                            # starts.
                            logger.warning(
                                "agent.yaml: unknown context key '%s' for "
                                "action %s/%s on agent '%s' — not a field "
                                "on %s or any base class; the override "
                                "will be silently dropped. Remove it from "
                                "agent.yaml or check for a typo against the "
                                "action's model_fields.",
                                key,
                                metadata.namespace,
                                metadata.name,
                                agent_name,
                                action_class.__name__,
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
            elif metadata.agent_namespace and metadata.agent_name:
                module_name = (
                    f"jvagent.actions.{metadata.agent_namespace}.{metadata.agent_name}."
                    f"{metadata.namespace}.{metadata.name}"
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

            ns = agent_namespace or getattr(metadata, "agent_namespace", "") or ""
            agent_dir = ""
            if ns and agent_name:
                candidate = self.base_path / "agents" / ns / agent_name
                if candidate.is_dir():
                    agent_dir = str(candidate)

            action.metadata = factory.build_action_metadata_payload(
                metadata=metadata,
                merged_config=merged_config,
                config_overrides=config_overrides,
                agent_name=agent_name,
                agent_namespace=ns,
                agent_dir=agent_dir,
                loaded_modules=loaded_modules,
            )

            action._property_override_keys = (
                set(property_overrides.keys()) if property_overrides else set()
            )

            _warn_if_anchorless_routable_ia(action, metadata, agent_name)

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
                    metadata.enabled = context.get("enabled", metadata.enabled)
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
                namespace,
                property_overrides=property_overrides,
                config_overrides=config_overrides,
            )

            if action:
                actions.append(action)

        return actions
