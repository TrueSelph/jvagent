"""Core (bundled) jvagent action path resolution and cache building."""

import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from jvagent.action.loader import info_yaml

logger = logging.getLogger(__name__)


def get_core_action_path(loader: Any) -> Optional[Path]:
    """Resolve path to the packaged ``jvagent/action`` directory."""
    if loader._core_action_path is not None and loader._core_action_path.exists():
        if info_yaml.has_info_yaml_files(loader._core_action_path):
            return loader._core_action_path
        loader._core_action_path = None
        loader._core_action_cache = None

    try:
        spec = importlib.util.find_spec("jvagent")
        if spec and spec.origin:
            jvagent_path = Path(spec.origin).parent
            action_path = jvagent_path / "action"
            if action_path.exists() and action_path.is_dir():
                loader._core_action_path = action_path
                logger.debug("Found core action path (installed): %s", action_path)
                return action_path
    except Exception as e:
        logger.debug("Could not find jvagent via importlib: %s", e)

    dev_paths = [
        loader.base_path.parent / "jvagent" / "action",
        loader.base_path.parent.parent / "jvagent" / "jvagent" / "action",
        Path(__file__).resolve().parent.parent,
    ]

    for dev_path in dev_paths:
        if dev_path.exists() and dev_path.is_dir():
            if info_yaml.has_info_yaml_files(dev_path):
                loader._core_action_path = dev_path
                logger.debug("Found core action path (dev): %s", dev_path)
                return dev_path

    logger.debug("Could not find core action path")
    return None


def build_core_action_cache(loader: Any) -> Dict[str, Dict[str, Any]]:
    """Scan core action tree for ``info.yaml`` files and build name → metadata map."""
    if loader._core_action_cache is not None:
        return loader._core_action_cache

    core_path = get_core_action_path(loader)
    if not core_path:
        loader._core_action_cache = {}
        return loader._core_action_cache

    action_cache: Dict[str, Dict[str, Any]] = {}

    for info_file in core_path.rglob("info.yaml"):
        if "__pycache__" in info_file.parts or any(
            part.startswith("_") for part in info_file.parts[:-1]
        ):
            continue

        data = info_yaml.load_info_yaml(info_file)
        if not data:
            continue

        package = data.get("package", {})
        if not isinstance(package, dict):
            continue

        full_name = package.get("name", "")
        if "/" not in full_name:
            continue

        namespace_part, action_name = full_name.split("/", 1)
        if namespace_part != "jvagent":
            continue

        class_name = package.get("archetype", "")
        if not class_name:
            continue

        action_dir = info_file.parent

        module_file = None
        base_file = action_dir / "base.py"
        if base_file.exists():
            module_file = "base"
        else:
            for py_file in action_dir.glob("*.py"):
                if py_file.name != "__init__.py":
                    module_file = py_file.stem
                    break

        if not module_file:
            module_file = action_dir.name

        try:
            relative_path = action_dir.relative_to(core_path)
            relative_path_str = str(relative_path).replace("\\", "/")
        except ValueError:
            relative_path_str = action_dir.name

        action_cache[action_name] = {
            "dir": action_dir,
            "module_file": module_file,
            "class_name": class_name,
            "relative_path": relative_path_str,
            "data": data,
            "info_file": info_file,
        }

        logger.debug(
            "Discovered core action: %s -> (dir=%s, module=%s, class=%s, path=%s)",
            action_name,
            action_dir.name,
            module_file,
            class_name,
            relative_path_str,
        )

    loader._core_action_cache = action_cache
    logger.debug("Built core action cache with %s actions", len(action_cache))
    return action_cache
