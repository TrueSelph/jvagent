"""HTTP bridge from LiveKit voice worker to jvagent Orchestrator."""

from __future__ import annotations

import logging
import os
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


def jvagent_base_url() -> str:
    """Base URL for jvagent interact API (no trailing slash)."""
    for key in (
        "JVAGENT_INTERNAL_BASE_URL",
        "JVAGENT_PUBLIC_BASE_URL",
        "JVFORGE_PUBLIC_BASE_URL",
    ):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    host = (os.environ.get("JVAGENT_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.environ.get("JVAGENT_PORT") or "8800").strip() or "8800"
    return f"http://{host}:{port}"


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
) -> str:
    """POST to jvagent /agents/{id}/interact and return the response text."""
    url = f"{jvagent_base_url()}/api/agents/{agent_id}/interact"
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
