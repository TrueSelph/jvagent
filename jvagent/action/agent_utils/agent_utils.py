"""Agent Utils action."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class AgentUtils(Action):
    """Action for agent utilities and power user controls."""

    async def list_interactions(self) -> List[Dict]:
        """List all interaction logs.

        Returns:
            List of interaction log contents (dictionaries).
        """
        # Default to jvspatial_logs matching the app structure
        log_db_name = os.environ.get("JVAGENT_LOG_DB_NAME", "jvagent_logs")

        # Resolve to absolute path if it is relative
        # This resolves relative to the current working directory (which is usually the app root)
        if not os.path.isabs(log_db_name):
            log_db_name = os.path.abspath(log_db_name)

        # Construct path: {log_db_name}/object
        log_path = Path(log_db_name) / "object"

        if not log_path.exists():
            logger.warning(f"Log path does not exist: {log_path}")

            # Fallback: Check if jvspatial_logs exists in the current directory or parent
            # This handles cases where .env has the old jvagent_logs name but the dir is actually jvspatial_logs
            fallback_path = Path("jvspatial_logs").resolve() / "object"
            if fallback_path.exists():
                logger.info(f"Fallback: Found logs at {fallback_path}")
                log_path = fallback_path
            else:
                return []

        if not log_path.is_dir():
            logger.warning(f"Log path is not a directory: {log_path}")
            return []

        interactions = []
        files = sorted(
            [f for f in log_path.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_ctime,  # creation time
            reverse=True,
        )

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    interactions.append(content)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON from {file_path}")
            except Exception as e:
                logger.warning(f"Error reading {file_path}: {e}")

        return interactions
