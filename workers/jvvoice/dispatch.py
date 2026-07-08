"""Resolve jvagent dispatch metadata from a LiveKit JobContext."""

from __future__ import annotations

import logging
from typing import Any, Dict

from livekit import rtc
from livekit.agents import JobContext

from jvagent_bridge import parse_dispatch_metadata

logger = logging.getLogger(__name__)


class MissingDispatchMetadata(ValueError):
    """Raised when a call arrives without the required jvagent dispatch metadata."""


def job_dispatch_metadata(ctx: JobContext) -> str:
    """Return agent-dispatch metadata JSON from the LiveKit job, if present."""
    raw = getattr(ctx.job, "metadata", None)
    if raw and str(raw).strip():
        return str(raw).strip()
    return ""


async def resolve_call_context(ctx: JobContext) -> Dict[str, Any]:
    """Build orchestrator bridge context from job metadata and WhatsApp participant."""
    meta = parse_dispatch_metadata(job_dispatch_metadata(ctx))
    caller_phone = str(meta.get("caller_phone") or "").strip()
    whatsapp_call_id = str(meta.get("whatsapp_call_id") or "").strip()
    agent_id = str(meta.get("jvagent_agent_id") or "").strip()
    jvagent_base = str(meta.get("jvagent_base_url") or "").strip()

    try:
        participant = await ctx.wait_for_participant(
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR
        )
    except Exception as exc:
        logger.debug("No WhatsApp connector participant yet: %s", exc)
        participant = None

    if participant is not None:
        if not caller_phone:
            identity = (participant.identity or "").strip()
            if identity and not identity.startswith("whatsapp-call"):
                caller_phone = identity
        if participant.metadata:
            pmeta = parse_dispatch_metadata(participant.metadata)
            agent_id = agent_id or str(pmeta.get("jvagent_agent_id") or "").strip()
            caller_phone = caller_phone or str(pmeta.get("caller_phone") or "").strip()
            whatsapp_call_id = whatsapp_call_id or str(
                pmeta.get("whatsapp_call_id") or ""
            ).strip()
            jvagent_base = jvagent_base or str(
                pmeta.get("jvagent_base_url") or ""
            ).strip()

    if not agent_id:
        raise MissingDispatchMetadata(
            "jvagent_agent_id missing from LiveKit dispatch metadata; "
            "ensure the jvagent LiveKitWhatsAppAction is accepting the call and "
            "sending agent metadata"
        )

    if not jvagent_base:
        raise MissingDispatchMetadata(
            "jvagent_base_url missing from LiveKit dispatch metadata; "
            "set JVAGENT_PUBLIC_BASE_URL (or jvagent_base_url) on the jvagent agent "
            "so the worker knows which host to call"
        )

    if not caller_phone:
        caller_phone = "unknown"

    return {
        "jvagent_agent_id": agent_id,
        "jvagent_base_url": jvagent_base,
        "caller_phone": caller_phone,
        "whatsapp_call_id": whatsapp_call_id,
        "room_name": ctx.room.name,
    }
