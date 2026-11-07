"""Validate declarative host instructions; the SDK owns prompt execution."""

from typing import Any


def effective_instructions(action: Any, channel: str) -> str:
    """Append only the exact channel's literal instructions.

    Channel is a routing fact, not an authorization boundary. These declarations
    cannot change tools, permissions, providers, budgets or skill ownership.
    Validate all profiles, including unmatched ones, before selecting any text.
    """
    base = action.instructions
    profiles = getattr(action, "channel_instructions", {})
    if not isinstance(base, str) or not isinstance(profiles, dict):
        raise ValueError("Native instructions require literal text declarations")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("A nonempty channel label is required")
    for name, text in profiles.items():
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or not isinstance(text, str)
        ):
            raise ValueError(
                "Channel instructions require exact labels and literal text"
            )
    return "\n\n".join(part for part in (base, profiles.get(channel, "")) if part)
