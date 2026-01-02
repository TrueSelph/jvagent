"""INTERACTION log level registration for jvagent.

This module registers the INTERACTION custom log level (level 22) for jvagent.
The level is automatically registered when this module is imported.

For logging interactions, use the standard logger with the interaction() method:
    logger.interaction("Interaction message", extra={"event_code": "interaction_completed", ...})

The jvspatial BaseLoggingService is available if you need a custom logging service.
"""

# Register INTERACTION custom log level (level 22, between INFO=20 and CUSTOM=25)
from jvspatial.logging.custom_levels import add_custom_log_level

INTERACTION_LEVEL_NUMBER = add_custom_log_level("INTERACTION", 22, "interaction")


__all__ = [
    "INTERACTION_LEVEL_NUMBER",
]

