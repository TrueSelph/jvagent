"""In-process sender facts established by authenticated channel adapters.

These facts are host authority, not a wire credential or a grant of tool access.
Adapters must authenticate their ingress before calling the binding function.
"""

import weakref
from dataclasses import dataclass, field
from typing import Any

from jvagent.tooling.context import get_dispatch_context, require_tool_visitor


@dataclass(frozen=True)
class VerifiedChannelSender:
    """Sender facts scoped to one live adapter-created visitor."""

    agent_id: str
    sender: str
    channel: str
    session_id: str | None
    owner: weakref.ReferenceType[Any] = field(repr=False)


def bind_verified_channel_sender(
    visitor: Any, *, agent_id: str, sender: str, channel: str
) -> None:
    """Bind authenticated adapter facts before spawning its new visitor.

    Never call this based on request ``channel``/``data`` or model arguments.
    Public HTTP visitors cannot acquire authority through this helper. The
    reference expires with its visitor; it cannot authorize another turn.
    """
    if (
        not all(
            isinstance(value, str) and value.strip()
            for value in (agent_id, sender, channel)
        )
        or visitor.agent_id != agent_id
        or visitor.user_id != sender
        or visitor.channel != channel
        or getattr(visitor, "_public_identity", None) is not None
        or getattr(visitor, "_verified_channel_sender", None) is not None
    ):
        raise ValueError("Channel sender facts do not match the adapter visitor")
    visitor._verified_channel_sender = VerifiedChannelSender(
        agent_id=agent_id,
        sender=sender,
        channel=channel,
        session_id=visitor.session_id,
        owner=weakref.ref(visitor),
    )


def require_verified_channel_sender(*, channels: frozenset[str]) -> str:
    """Return the authenticated sender only within the unchanged dispatch."""
    visitor = require_tool_visitor()
    context = get_dispatch_context()
    proof = getattr(visitor, "_verified_channel_sender", None)
    if (
        not isinstance(proof, VerifiedChannelSender)
        or proof.owner() is not visitor
        or getattr(visitor, "_public_identity", None) is not None
        or context is None
        or proof.agent_id != context.agent_id
        or proof.agent_id != visitor.agent_id
        or proof.sender != context.user_id
        or proof.channel != context.channel
        or proof.channel not in channels
        or (proof.session_id is not None and proof.session_id != context.session_id)
    ):
        raise ValueError("Verified channel sender is required")
    return proof.sender


def verified_channel_metadata(*, channels: frozenset[str]) -> dict[str, str]:
    """Project verified host facts for SDK registration or immediate notices."""
    sender = require_verified_channel_sender(channels=channels)
    context = get_dispatch_context()
    assert context is not None
    facts = {
        "jv_verified_channel_sender": sender,
        "jv_verified_channel": context.channel,
        "jv_interaction_id": context.interaction_id,
        "jv_agent_id": context.agent_id,
        "jv_user_id": context.user_id,
        "jv_session_id": context.session_id,
    }
    if not all(isinstance(value, str) and value for value in facts.values()):
        raise ValueError("Channel sender metadata requires complete host scope")
    return {key: str(value) for key, value in facts.items()}
