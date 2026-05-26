"""Action metadata and registry for the action loader."""

from pathlib import Path
from typing import Any, Dict, Optional, Set


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

        package = data.get("package", {})
        if not isinstance(package, dict):
            package = {}

        package_name = package.get("name", "")
        if package_name and "/" in package_name:
            parsed_namespace, parsed_name = package_name.split("/", 1)
            if parsed_namespace == namespace or not namespace:
                self.namespace = parsed_namespace
            self.name = parsed_name
        else:
            self.name = package.get("name", data.get("name", ""))
            if not self.name:
                self.name = path.name

        self.author = package.get("author", "")
        self.archetype = package.get("archetype", "Action")
        self.version = package.get("version", "0.0.1")

        meta = package.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        self.title = meta.get("title", self.name.replace("_", " ").title())
        self.description = meta.get("description", "")
        self.group = meta.get("group", "")
        self.type = meta.get("type", "action")

        self.config = package.get("config", {})
        self.dependencies = package.get("dependencies", {})

        # Pattern-agnostic manifest block (BRIDGE-ROADMAP §D / ADR-0007 v0).
        # Located under ``package.manifest`` so it sits alongside ``meta``
        # and ``config``. Optional — missing yields an empty dict here and
        # safe defaults at ``Action.get_manifest()`` resolve time.
        manifest_block = package.get("manifest")
        if not isinstance(manifest_block, dict) and manifest_block is not None:
            # Defer schema validation to the manifest module so we don't
            # introduce a loader-level import cycle. Just preserve the raw
            # payload here.
            pass
        self.manifest = manifest_block if isinstance(manifest_block, dict) else None

        self.module = self.name
        self.class_name = self.archetype
        self.enabled = True

        self.is_core_action: bool = False
        self.core_module_path: Optional[str] = None
        self.core_class_name: Optional[str] = None

        self.agent_namespace: Optional[str] = None
        self.agent_name: Optional[str] = None

    def __repr__(self) -> str:
        return f"ActionMetadata(namespace={self.namespace}, name={self.name}, version={self.version})"


class ActionRegistry:
    """Registry for tracking required, resolved, and imported actions."""

    def __init__(self) -> None:
        self.required_actions: Set[str] = set()
        self.resolved_actions: Set[str] = set()
        self.imported_actions: Set[str] = set()
        self.action_metadata: Dict[str, ActionMetadata] = {}
        self._resolving: Set[str] = set()

    def add_required_action(self, action_ref: str) -> None:
        if action_ref and "/" in action_ref:
            self.required_actions.add(action_ref)

    def should_import_action(self, action_ref: str) -> bool:
        return (
            action_ref in self.resolved_actions
            and action_ref not in self.imported_actions
        )

    def mark_imported(self, action_ref: str) -> None:
        self.imported_actions.add(action_ref)

    def is_resolving(self, action_ref: str) -> bool:
        return action_ref in self._resolving

    def start_resolving(self, action_ref: str) -> None:
        self._resolving.add(action_ref)

    def finish_resolving(self, action_ref: str) -> None:
        self._resolving.discard(action_ref)
