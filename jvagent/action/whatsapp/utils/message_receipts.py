"""Provider acceptance is distinct from delivery and from a successful HTTP call."""

from typing import Any


def acknowledged_message_id(result: dict[str, Any]) -> str:
    """Require one bounded provider message ID without coercing malformed data."""
    messages = result.get("messages")
    first = messages[0] if isinstance(messages, list) and len(messages) == 1 else None
    value = first.get("id") if isinstance(first, dict) else None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or any(not 33 <= ord(char) <= 126 for char in value)
    ):
        raise ValueError("WhatsApp provider acknowledgement is missing or malformed")
    return value
