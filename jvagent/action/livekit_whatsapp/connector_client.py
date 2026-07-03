"""Thin wrapper around livekit-api ConnectorService for WhatsApp calls."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)


class LiveKitConnectorError(Exception):
    """Raised when a LiveKit Connector API call fails."""


class LiveKitConnectorClient:
    """Accept, connect, dial, and disconnect WhatsApp calls via LiveKit."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret

    @asynccontextmanager
    async def _api_client(self) -> AsyncIterator[Any]:
        try:
            from livekit import api as lk_api
        except ImportError as exc:
            raise LiveKitConnectorError(
                "livekit-api is not installed; pip install 'jvagent[livekit]'"
            ) from exc
        lk = lk_api.LiveKitAPI(
            url=self._url,
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        try:
            yield lk
        finally:
            await lk.aclose()

    async def close(self) -> None:
        """No-op; each API call uses a short-lived client session."""

    async def accept_whatsapp_call(
        self,
        *,
        whatsapp_phone_number_id: str,
        whatsapp_api_key: str,
        whatsapp_cloud_api_version: str,
        whatsapp_call_id: str,
        sdp: str,
        sdp_type: str,
        room_name: str,
        agent_name: str,
        agent_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Accept an inbound WhatsApp call and dispatch a LiveKit agent."""
        from livekit import api as lk_api
        from livekit.protocol.agent_dispatch import RoomAgentDispatch
        from livekit.protocol.rtc import SessionDescription

        metadata_json = json.dumps(agent_metadata or {})
        agents: List[RoomAgentDispatch] = [
            RoomAgentDispatch(
                agent_name=agent_name,
                metadata=metadata_json,
            )
        ]
        request = lk_api.AcceptWhatsAppCallRequest(
            whatsapp_phone_number_id=whatsapp_phone_number_id,
            whatsapp_api_key=whatsapp_api_key,
            whatsapp_cloud_api_version=whatsapp_cloud_api_version,
            whatsapp_call_id=whatsapp_call_id,
            sdp=SessionDescription(type=sdp_type or "offer", sdp=sdp),
            room_name=room_name,
            agents=agents,
            participant_metadata=metadata_json,
        )
        async with self._api_client() as lk:
            response = await lk.connector.accept_whatsapp_call(request)
        return {
            "room_name": getattr(response, "room_name", None) or room_name,
            "whatsapp_call_id": whatsapp_call_id,
        }

    async def disconnect_whatsapp_call(
        self,
        *,
        whatsapp_call_id: str,
        whatsapp_api_key: str = "",
        user_initiated: bool = True,
    ) -> None:
        """Disconnect an active WhatsApp call and clean up LiveKit resources."""
        from livekit import api as lk_api

        reason = (
            lk_api.DisconnectWhatsAppCallRequest.USER_INITIATED
            if user_initiated
            else lk_api.DisconnectWhatsAppCallRequest.BUSINESS_INITIATED
        )
        if user_initiated:
            request = lk_api.DisconnectWhatsAppCallRequest(
                whatsapp_call_id=whatsapp_call_id,
                disconnect_reason=reason,
            )
        else:
            request = lk_api.DisconnectWhatsAppCallRequest(
                whatsapp_call_id=whatsapp_call_id,
                whatsapp_api_key=whatsapp_api_key,
                disconnect_reason=reason,
            )
        try:
            async with self._api_client() as lk:
                await lk.connector.disconnect_whatsapp_call(request)
        except Exception as exc:
            raise LiveKitConnectorError(str(exc)) from exc
