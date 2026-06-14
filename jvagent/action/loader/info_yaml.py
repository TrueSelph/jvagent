"""info.yaml discovery and parsing for action packages."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from jvagent.core.env_resolver import resolve_env_placeholders

logger = logging.getLogger(__name__)


def has_info_yaml_files(path: Path) -> bool:
    """Return True if the tree under ``path`` contains any action ``info.yaml`` files."""
    return any(
        info_file.exists()
        for info_file in path.rglob("info.yaml")
        if "__pycache__" not in info_file.parts
        and not any(part.startswith("_") for part in info_file.parts[:-1])
    )


def load_info_yaml(info_file: Path) -> Optional[Dict[str, Any]]:
    """Load and parse ``info.yaml`` with environment variable resolution."""
    try:
        with open(info_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            return None

        return resolve_env_placeholders(data)

    except Exception as e:
        logger.debug("Error loading info.yaml from %s: %s", info_file, e)
        return None


def extract_action_name(package: Dict[str, Any], action_dir: Path) -> str:
    """Derive action name from package metadata or directory name."""
    package_name = package.get("name", "")
    if package_name and "/" in package_name:
        _, action_name = package_name.split("/", 1)
        return action_name
    return package.get("name", action_dir.name)
