"""LiveKit WhatsApp voice call action."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool

from .call_webhook import WhatsAppCallEvent, parse_calls_webhook
from .connector_client import LiveKitConnectorClient, LiveKitConnectorError

logger = logging.getLogger(__name__)

_CALL_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


class LiveKitWhatsAppAction(Action):
    """Accept and manage WhatsApp voice calls via LiveKit Connector.

    Requires a sibling ``WhatsAppAction`` with ``provider: meta`` on the same agent
    for Meta credentials (phone_number_id, access_token). LiveKit server credentials
    come from action attributes or ``LIVEKIT_URL`` / ``LIVEKIT_API_KEY`` /
    ``LIVEKIT_API_SECRET`` environment variables.

    A separate standalone jvvoice agent (see ``workers/jvvoice/README.md``)
    must be running and registered under ``agent_name`` to handle realtime audio and
    bridge utterances to the jvagent Orchestrator.
    """

    livekit_url: str = attribute(
        default="",
        description="LiveKit server URL (wss://…); when empty, LIVEKIT_URL env is used",
    )
    livekit_api_key: str = attribute(
        default="",
        description="LiveKit API key; when empty, LIVEKIT_API_KEY env is used",
    )
    livekit_api_secret: str = attribute(
        default="",
        description="LiveKit API secret; when empty, LIVEKIT_API_SECRET env is used",
    )
    agent_name: str = attribute(
        default="jvvoice",
        description="LiveKit agent dispatch name for jvvoice",
    )
    cloud_api_version: str = attribute(
        default="24.0",
        description="WhatsApp Cloud API version for LiveKit Connector (23.0 or 24.0)",
        pattern=r"^(23\.0|24\.0)$",
    )
    whatsapp_action: str = attribute(
        default="WhatsAppAction",
        description="Class name of the sibling WhatsAppAction for Meta credentials",
    )
    room_name_prefix: str = attribute(
        default="whatsapp-call",
        description="Prefix for LiveKit room names created per call",
    )
    jvagent_base_url: str = attribute(
        default="",
        description=(
            "jvagent base URL for jvvoice /interact callbacks; "
            "when empty, JVAGENT_BASE_URL / JVAGENT_PUBLIC_BASE_URL env is used"
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._active_calls: Dict[str, str] = {}
        self._connector: Optional[LiveKitConnectorClient] = None

    @staticmethod
    def _env_livekit_url() -> str:
        return (env("LIVEKIT_URL") or "").strip()

    @staticmethod
    def _env_livekit_api_key() -> str:
        return (env("LIVEKIT_API_KEY") or "").strip()

    @staticmethod
    def _env_livekit_api_secret() -> str:
        return (env("LIVEKIT_API_SECRET") or "").strip()

    @staticmethod
    def _env_jvagent_base_url() -> str:
        for key in (
            "JVAGENT_BASE_URL",
            "JVAGENT_INTERNAL_BASE_URL",
            "JVAGENT_PUBLIC_BASE_URL",
            "JVFORGE_PUBLIC_BASE_URL",
        ):
            value = (env(key) or "").strip()
            if value:
                return value.rstrip("/")
        return ""

    def _resolved_livekit_url(self) -> str:
        return (self.livekit_url or self._env_livekit_url()).strip()

    def _resolved_livekit_api_key(self) -> str:
        return (self.livekit_api_key or self._env_livekit_api_key()).strip()

    def _resolved_livekit_api_secret(self) -> str:
        return (self.livekit_api_secret or self._env_livekit_api_secret()).strip()

    def _resolved_jvagent_base_url(self) -> str:
        return (self.jvagent_base_url or self._env_jvagent_base_url()).strip().rstrip("/")

    def is_configured(self) -> bool:
        """Return True when LiveKit credentials and dispatch name are set."""
        return bool(
            self._resolved_livekit_url()
            and self._resolved_livekit_api_key()
            and self._resolved_livekit_api_secret()
            and (self.agent_name or "").strip()
        )

    def get_capabilities(self) -> List[str]:
        """Return voice-call capabilities for PersonaAction when enabled."""
        if not self.enabled or not self.is_configured():
            return []
        return [
            "Answer inbound WhatsApp voice calls via LiveKit",
            "Conduct realtime voice conversations bridged to the agent Orchestrator",
        ]

    def _room_name_for_call(self, call_id: str) -> str:
        suffix = _CALL_ID_SAFE.sub("-", call_id)[-24:].strip("-") or "call"
        return f"{self.room_name_prefix}-{suffix}"

    async def _get_whatsapp_action(self) -> Any:
        wa = await self.get_action(self.whatsapp_action)
        if wa is None:
            raise ValueError(
                f"Sibling {self.whatsapp_action!r} not found on this agent"
            )
        if not getattr(wa, "is_meta_provider", lambda: False)():
            raise ValueError(
                f"{self.whatsapp_action} must use provider 'meta' for LiveKit calls"
            )
        return wa

    async def _meta_credentials(self) -> tuple[str, str]:
        wa = await self._get_whatsapp_action()
        phone_number_id = wa._env_phone_number_id()
        access_token = wa._env_access_token()
        if not phone_number_id or not access_token:
            raise ValueError(
                "Meta phone_number_id and access_token are required "
                "(WhatsAppAction yaml or WHATSAPP_* env)"
            )
        return phone_number_id, access_token

    async def _connector_client(self) -> LiveKitConnectorClient:
        if self._connector is None:
            url = self._resolved_livekit_url()
            api_key = self._resolved_livekit_api_key()
            api_secret = self._resolved_livekit_api_secret()
            if not url or not api_key or not api_secret:
                raise LiveKitConnectorError(
                    "LiveKit URL, API key, and API secret must be configured"
                )
            self._connector = LiveKitConnectorClient(
                url=url,
                api_key=api_key,
                api_secret=api_secret,
            )
        return self._connector

    async def handle_call_webhook(
        self,
        request: Dict[str, Any],
        *,
        agent_id: str,
    ) -> Dict[str, Any]:
        """Process Meta ``field=calls`` webhook events (connect / terminate)."""
        events = parse_calls_webhook(request)
        if not events:
            return {"status": "ignored", "response": "no call events"}

        results: List[Dict[str, Any]] = []
        for event in events:
            if event.event == "connect":
                results.append(await self._handle_connect(event, agent_id=agent_id))
            elif event.event == "terminate":
                results.append(await self._handle_terminate(event))
            else:
                logger.debug(
                    "Ignoring WhatsApp call event %r for call_id=%s",
                    event.event,
                    event.call_id,
                )
                results.append(
                    {
                        "status": "ignored",
                        "event": event.event,
                        "call_id": event.call_id,
                    }
                )

        if len(results) == 1:
            return results[0]
        return {"status": "ok", "results": results}

    async def _handle_connect(
        self,
        event: WhatsAppCallEvent,
        *,
        agent_id: str,
    ) -> Dict[str, Any]:
        if not event.sdp:
            logger.error(
                "WhatsApp connect webhook missing SDP for call_id=%s",
                event.call_id,
            )
            return {
                "status": "error",
                "call_id": event.call_id,
                "error": "missing_sdp",
            }

        phone_number_id, access_token = await self._meta_credentials()
        if event.phone_number_id and event.phone_number_id != phone_number_id:
            logger.warning(
                "Call phone_number_id %s does not match configured %s",
                event.phone_number_id,
                phone_number_id,
            )

        room_name = self._room_name_for_call(event.call_id)
        agent_metadata: Dict[str, Any] = {
            "jvagent_agent_id": agent_id,
            "caller_phone": event.from_number,
            "caller_name": event.contact_name,
            "whatsapp_call_id": event.call_id,
        }
        base_url = self._resolved_jvagent_base_url()
        if base_url:
            agent_metadata["jvagent_base_url"] = base_url

        try:
            client = await self._connector_client()
            result = await client.accept_whatsapp_call(
                whatsapp_phone_number_id=phone_number_id,
                whatsapp_api_key=access_token,
                whatsapp_cloud_api_version=self.cloud_api_version,
                whatsapp_call_id=event.call_id,
                sdp=event.sdp,
                sdp_type=event.sdp_type or "offer",
                room_name=room_name,
                agent_name=self.agent_name,
                agent_metadata=agent_metadata,
            )
            self._active_calls[event.call_id] = result.get("room_name") or room_name
            logger.info(
                "Accepted WhatsApp call call_id=%s room=%s agent=%s",
                event.call_id,
                self._active_calls[event.call_id],
                self.agent_name,
            )
            return {
                "status": "connected",
                "call_id": event.call_id,
                "room_name": self._active_calls[event.call_id],
            }
        except (LiveKitConnectorError, ValueError) as exc:
            logger.error(
                "Failed to accept WhatsApp call call_id=%s: %s",
                event.call_id,
                exc,
                exc_info=True,
            )
            return {
                "status": "error",
                "call_id": event.call_id,
                "error": str(exc),
            }

    async def _handle_terminate(self, event: WhatsAppCallEvent) -> Dict[str, Any]:
        self._active_calls.pop(event.call_id, None)
        try:
            client = await self._connector_client()
            await client.disconnect_whatsapp_call(
                whatsapp_call_id=event.call_id,
                user_initiated=True,
            )
            logger.info(
                "Disconnected WhatsApp call call_id=%s (user initiated)",
                event.call_id,
            )
            return {"status": "disconnected", "call_id": event.call_id}
        except LiveKitConnectorError as exc:
            logger.warning(
                "DisconnectWhatsAppCall failed for call_id=%s: %s",
                event.call_id,
                exc,
            )
            # User hang-up cleanup is best-effort; LiveKit also auto-cleans stale rooms.
            return {
                "status": "disconnected",
                "call_id": event.call_id,
                "warning": str(exc),
            }

    @tool
    async def end_whatsapp_call(self, call_id: str) -> Dict[str, Any]:
        """End an active WhatsApp voice call (business-initiated hangup)."""
        call_id = (call_id or "").strip()
        if not call_id:
            return {"ok": False, "error": "call_id is required"}
        _, access_token = await self._meta_credentials()
        try:
            client = await self._connector_client()
            await client.disconnect_whatsapp_call(
                whatsapp_call_id=call_id,
                whatsapp_api_key=access_token,
                user_initiated=False,
            )
            self._active_calls.pop(call_id, None)
            return {"ok": True, "call_id": call_id}
        except (LiveKitConnectorError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "call_id": call_id}

    async def on_deregister(self) -> None:
        if self._connector is not None:
            await self._connector.close()
            self._connector = None
        await super().on_deregister()
