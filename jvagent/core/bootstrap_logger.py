"""Concise bootstrap logging utilities for jvagent.

This module provides utilities for logging bootstrap operations in a concise
but informative manner, grouping related operations and showing summaries.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BootstrapLogger:
    """Logger for bootstrap operations with concise, grouped output."""

    def __init__(self, context: str = "Bootstrap"):
        """Initialize bootstrap logger.

        Args:
            context: Context name for the bootstrap operation
        """
        self.context = context
        self._logger = logging.getLogger(__name__)
        self._stats: Dict[str, Any] = {}

    def start(self, message: str) -> None:
        """Log start of a bootstrap phase.

        Args:
            message: Start message
        """
        self._logger.info(f"🚀 {self.context}: {message}")

    def complete(self, message: Optional[str] = None) -> None:
        """Log completion of bootstrap phase.

        Args:
            message: Optional completion message
        """
        if message:
            self._logger.info(f"✓ {self.context}: {message}")
        else:
            self._logger.info(f"✓ {self.context}: Complete")

    def summary(self, items: Dict[str, Any]) -> None:
        """Log a summary of bootstrap operations.

        Args:
            items: Dictionary of summary items to display
        """
        parts = []
        for key, value in items.items():
            if value is not None and value != 0:
                if isinstance(value, (int, float)):
                    parts.append(f"{key}: {value}")
                else:
                    parts.append(f"{key}: {value}")

        if parts:
            summary = " | ".join(parts)
            self._logger.info(f"📊 {self.context}: {summary}")

    def info(self, message: str) -> None:
        """Log an info message.

        Args:
            message: Info message
        """
        self._logger.info(f"  {message}")

    def warning(self, message: str) -> None:
        """Log a warning message.

        Args:
            message: Warning message
        """
        self._logger.warning(f"⚠️  {message}")

    def error(self, message: str) -> None:
        """Log an error message.

        Args:
            message: Error message
        """
        self._logger.error(f"❌ {message}")


def log_bootstrap_summary(
    app_name: Optional[str] = None,
    app_version: Optional[str] = None,
    agents_installed: int = 0,
    agents_updated: int = 0,
    actions_registered: int = 0,
    actions_updated: int = 0,
    duplicates_removed: int = 0,
) -> None:
    """Log a concise bootstrap summary.

    Args:
        app_name: Application name
        app_version: Application version
        agents_installed: Number of agents installed
        agents_updated: Number of agents updated
        actions_registered: Number of actions registered
        actions_updated: Number of actions updated
        duplicates_removed: Number of duplicate/orphan actions removed
    """
    parts = []
    if app_name:
        version_str = f" v{app_version}" if app_version else ""
        parts.append(f"App: {app_name}{version_str}")

    if agents_installed > 0 or agents_updated > 0:
        agent_parts = []
        if agents_installed > 0:
            agent_parts.append(f"{agents_installed} installed")
        if agents_updated > 0:
            agent_parts.append(f"{agents_updated} updated")
        parts.append(f"Agents: {', '.join(agent_parts)}")

    if actions_registered > 0 or actions_updated > 0:
        action_parts = []
        if actions_registered > 0:
            action_parts.append(f"{actions_registered} registered")
        if actions_updated > 0:
            action_parts.append(f"{actions_updated} updated")
        parts.append(f"Actions: {', '.join(action_parts)}")

    if duplicates_removed > 0:
        parts.append(f"Cleaned: {duplicates_removed} duplicates/orphans")

    if parts:
        summary = " | ".join(parts)
        logger.info(f"📊 Bootstrap: {summary}")
    else:
        logger.info("📊 Bootstrap: Ready")
