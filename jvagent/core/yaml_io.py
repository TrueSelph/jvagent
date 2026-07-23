"""YAML I/O utilities for jvagent.

Provides synchronous YAML loading helpers for bootstrap and configuration code
that cannot use async I/O.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Union

import yaml

logger = logging.getLogger(__name__)


def load_yaml_sync(path: Union[str, Path]) -> Dict[str, Any]:
    """Load a YAML file synchronously and return parsed content.

    Args:
        path: Path to YAML file

    Returns:
        Parsed YAML content as a dictionary

    Raises:
        FileNotFoundError: If path does not exist
        yaml.YAMLError: If YAML parsing fails
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                return {}
            if not isinstance(data, dict):
                logger.warning(
                    f"YAML file {path} did not parse to a dict, got {type(data).__name__}"
                )
                return {}
            return data
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML from {path}: {e}")
        raise
    except Exception as e:
        logger.error(f"Error reading YAML file {path}: {e}")
        raise
