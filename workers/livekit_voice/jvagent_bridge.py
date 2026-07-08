"""HTTP bridge from LiveKit voice worker to jvagent Orchestrator."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL = "whatsapp_call"


def extract_interact_response_text(body: Any) -> Optional[str]:
    """Extract assistant text from jvagent interact JSON (several wrapper shapes)."""
    if not isinstance(body, dict):
        return None

    response = body.get("response")
    if isinstance(response, str) and response.strip():
        return response.strip()

    data = body.get("data")
    if isinstance(data, dict):
        nested = data.get("response")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        interaction = data.get("interaction")
        if isinstance(interaction, dict):
            nested = interaction.get("response")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()

    interaction = body.get("interaction")
    if isinstance(interaction, dict):
        nested = interaction.get("response")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()

    return None


def jvagent_base_url(override: Optional[str] = None) -> str:
    """Base URL for jvagent interact API (no trailing slash).

    Resolved per call from dispatch metadata (``jvagent_base_url``). There is no
    env fallback: each jvagent instance sends its own base URL when accepting a
    call, so a missing value is a configuration error rather than something to
    guess at.
    """
    custom = (override or "").strip().rstrip("/")
    if not custom:
        raise ValueError(
            "jvagent_base_url is required (per-call dispatch metadata); "
            "set JVAGENT_PUBLIC_BASE_URL (or jvagent_base_url) on the jvagent agent"
        )
    return custom


async def interact(
    *,
    agent_id: str,
    utterance: str,
    user_id: str,
    session_id: str,
    room_name: str = "",
    whatsapp_call_id: str = "",
    call_active: bool = True,
    timeout: float = 120.0,
    jvagent_base_url_override: str = "",
) -> str:
    """POST to jvagent /agents/{id}/interact and return the response text."""
    base = jvagent_base_url(jvagent_base_url_override)
    url = f"{base}/api/agents/{agent_id}/interact"
    logger.debug("jvagent interact POST %s", url)
    data_payload: Dict[str, Any] = {
        "livekit_room": room_name,
        "whatsapp_call_id": whatsapp_call_id,
        "call_active": call_active,
    }
    payload = {
        "utterance": utterance,
        "channel": DEFAULT_CHANNEL,
        "user_id": user_id,
        "session_id": session_id,
        "stream": False,
        "data": data_payload,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
    text = extract_interact_response_text(body)
    if text:
        return text
    logger.warning("jvagent interact returned empty response for agent_id=%s", agent_id)
    return "I'm sorry, I didn't get a response. Could you try again?"


def session_id_for_caller(phone: str) -> str:
    """Stable session id for a caller's voice conversation."""
    clean = (phone or "unknown").strip() or "unknown"
    return f"whatsapp-call:{clean}"


def parse_dispatch_metadata(metadata: Optional[str]) -> Dict[str, Any]:
    """Parse RoomAgentDispatch metadata JSON from LiveKitWhatsAppAction."""
    if not metadata:
        return {}
    try:
        import json

        parsed = json.loads(metadata)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
