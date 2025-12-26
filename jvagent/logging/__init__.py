"""Logging system for jvagent interactions."""

from jvagent.logging.config import get_logging_config, initialize_logging_database
from jvagent.logging.service import LoggingService, get_logging_service
from jvagent.logging.archive import ArchiveService, get_archive_service
from jvagent.logging.retention import RetentionTask, get_retention_task
from jvagent.logging.models import InteractionLog

# Conditionally import endpoints only if logging is enabled globally
# App-level checks are performed at runtime in each endpoint
_logging_config = get_logging_config()
if _logging_config.get("enabled", True):
    # Import endpoints to ensure they are registered
    from jvagent.logging import endpoints  # noqa: F401

__all__ = [
    "get_logging_config",
    "initialize_logging_database",
    "LoggingService",
    "get_logging_service",
    "ArchiveService",
    "get_archive_service",
    "RetentionTask",
    "get_retention_task",
    "InteractionLog",
]

