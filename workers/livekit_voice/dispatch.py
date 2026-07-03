"""Resolve jvagent dispatch metadata from a LiveKit JobContext."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from livekit import rtc
from livekit.agents import JobContext

from .jvagent_bridge import parse_dispatch_metadata

logger = logging.getLogger(__name__)


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

    if not agent_id:
        agent_id = (os.environ.get("JVAGENT_AGENT_ID") or "").strip()

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
        if participant.metadata and not meta:
            meta = parse_dispatch_metadata(participant.metadata)
            agent_id = agent_id or str(meta.get("jvagent_agent_id") or "").strip()
            caller_phone = caller_phone or str(meta.get("caller_phone") or "").strip()
            whatsapp_call_id = whatsapp_call_id or str(
                meta.get("whatsapp_call_id") or ""
            ).strip()

    if not agent_id:
        raise ValueError(
            "jvagent_agent_id missing from job metadata and JVAGENT_AGENT_ID is unset"
        )

    if not caller_phone:
        caller_phone = "unknown"

    return {
        "jvagent_agent_id": agent_id,
        "caller_phone": caller_phone,
        "whatsapp_call_id": whatsapp_call_id,
        "room_name": ctx.room.name,
    }
