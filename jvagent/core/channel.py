"""Channel normalization utilities and the channel topology config lints use.

The canonical channel for web/default UI is 'default'. When no channel is specified
or 'web' is used, it normalizes to 'default'.

Channels are an **open set**: ``/interact`` accepts ``channel`` as a free-form
query parameter, so any caller can introduce one. The tables below are therefore
advisory metadata for validation, never an allow-list — an unrecognized channel
is legitimate and must never be rejected.
"""

from typing import Dict, FrozenSet, Optional, Tuple

# Channels that arrive as separate strings but describe one integration. A knob
# set for one member and not the other is usually an oversight: the operator
# thinks of "WhatsApp" as one thing, but a voice turn arrives on
# ``whatsapp_call`` and a chat turn on ``whatsapp``, and ``channel_overrides``
# is looked up by the exact string.
CHANNEL_FAMILIES: Tuple[FrozenSet[str], ...] = (
    frozenset({"whatsapp", "whatsapp_call"}),
)

# Which first-party action reference makes a channel reachable on an agent. Used
# to decide whether a missing sibling override is worth mentioning: an agent with
# no voice action should never be told about ``whatsapp_call``. Third-party
# adapters are absent by design — an unknown provider yields no advisory rather
# than a wrong one.
# Keys are the canonical ``package.name`` from each action's info.yaml — NOT the
# directory name, which differs (``jvagent/action/whatsapp_voice/`` publishes as
# ``jvagent/whatsapp_voice_action``). agent.yaml references the package name.
CHANNEL_PROVIDERS: Dict[str, str] = {
    "whatsapp": "jvagent/whatsapp_action",
    "whatsapp_call": "jvagent/whatsapp_voice_action",
    "email": "jvagent/email_action",
    "messenger": "jvagent/facebook_action",
}

# Override keys whose absence on a channel changes behavior *silently*. Excluded
# deliberately: history_limit, ack knobs, system_prompt_extra — per-channel
# divergence there is normal and intentional, so flagging it would be noise.
COVERAGE_SENSITIVE_OVERRIDE_KEYS: Tuple[str, ...] = (
    "skill_only_tools",
    "denied_tools",
    "pinned_tools",
)


def channel_siblings(channel: str) -> FrozenSet[str]:
    """Other channels in *channel*'s family (empty when it has none)."""
    for family in CHANNEL_FAMILIES:
        if channel in family:
            return frozenset(family - {channel})
    return frozenset()


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
