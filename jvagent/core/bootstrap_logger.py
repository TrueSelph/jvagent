"""Concise bootstrap logging utilities for jvagent.

This module provides utilities for logging bootstrap operations in a concise
but informative manner, grouping related operations and showing summaries.
"""

import logging
from typing import Any, Dict, Optional

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
        self._logger.info(f"[START] {self.context}: {message}")

    def complete(self, message: Optional[str] = None) -> None:
        """Log completion of bootstrap phase.

        Args:
            message: Optional completion message
        """
        if message:
            self._logger.info(f"[OK] {self.context}: {message}")
        else:
            self._logger.info(f"[OK] {self.context}: Complete")

    def summary(self, items: Dict[str, Any]) -> None:
        """Log a summary of bootstrap operations.

        Args:
            items: Dictionary of summary items to display
        """
        parts = []
        for key, value in items.items():
            if value is not None and value != 0:
                parts.append(f"{key}: {value}")

        if parts:
            summary = " | ".join(parts)
            self._logger.info(f"[STATS] {self.context}: {summary}")

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
        self._logger.warning(f"[WARN] {message}")

    def error(self, message: str) -> None:
        """Log an error message.

        Args:
            message: Error message
        """
        self._logger.error(f"[ERR] {message}")
