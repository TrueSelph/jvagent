"""Channel normalization utilities.

The canonical channel for web/default UI is 'default'. When no channel is specified
or 'web' is used, it normalizes to 'default'.
"""

from typing import Optional


def normalize_channel(channel: Optional[str]) -> str:
    """Normalize channel to canonical form.

    Maps None, empty string, and 'web' to 'default'.
    default = web (the standard web UI channel).

    Args:
        channel: Raw channel value from request or config.

    Returns:
        Normalized channel string, always 'default' for web/default cases.
    """
    if channel is None or not isinstance(channel, str):
        return "default"
    s = channel.strip()
    if s == "" or s.lower() == "web":
        return "default"
    return s
