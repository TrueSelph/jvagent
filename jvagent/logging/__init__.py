"""Logging system for jvagent.

Provides:
1. INTERACTION custom log level for interaction logging
2. Custom endpoints for querying logs by agent_id and time frame

The INTERACTION level is automatically registered when this module is imported.
Use the standard logger with logger.interaction() to log interactions.

For custom logging services, use jvspatial's BaseLoggingService.
"""

# Import service to ensure INTERACTION level is registered
from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

# Import endpoints to ensure they are registered
from jvagent.logging import endpoints  # noqa: F401

__all__ = [
    "INTERACTION_LEVEL_NUMBER",
]

