"""WhatsApp voice call action (jvvoice delegation)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute
from jvspatial.env import env

from jvagent.action.base import Action
from jvagent.tooling.tool_decorator import tool

from .call_webhook import WhatsAppCallEvent, parse_calls_webhook
from .jvvoice_client import JvvoiceClient, JvvoiceClientError

logger = logging.getLogger(__name__)


class WhatsAppVoiceAction(Action):
    """Accept and manage WhatsApp voice calls via jvvoice delegation.

    Requires a sibling ``WhatsAppAction`` with ``provider: meta`` on the same agent
    for Meta credentials (phone_number_id, access_token). jvagent calls jvvoice's
    connector HTTP API using ``jvvoice_base_url`` and ``jvvoice_api_key``.

    A standalone jvvoice deployment must be running and registered under ``agent_name``
    to handle realtime audio and bridge utterances to the jvagent Orchestrator.
    """

    jvvoice_base_url: str = attribute(
        default="",
        description="jvvoice connector API base URL; when empty, JVVOICE_BASE_URL env is used",
    )
    jvvoice_api_key: str = attribute(
        default="",
        description="jvvoice API key; when empty, JVVOICE_API_KEY env is used",
    )
    agent_name: str = attribute(
        default="jvvoice",
        description="jvvoice worker registration name",
    )
    cloud_api_version: str = attribute(
        default="24.0",
        description="WhatsApp Cloud API version forwarded to jvvoice (23.0 or 24.0)",
        pattern=r"^(23\.0|24\.0)$",
    )
    whatsapp_action: str = attribute(
        default="WhatsAppAction",
        description="Class name of the sibling WhatsAppAction for Meta credentials",
    )
    jvagent_base_url: str = attribute(
        default="",
        description=(
            "jvagent base URL for jvvoice /interact callbacks; "
            "when empty, JVAGENT_PUBLIC_BASE_URL env is used"
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._active_calls: Dict[str, str] = {}
        self._jvvoice: Optional[JvvoiceClient] = None

    @staticmethod
    def _env_jvvoice_base_url() -> str:
        return (env("JVVOICE_BASE_URL") or "").strip().rstrip("/")

    @staticmethod
    def _env_jvvoice_api_key() -> str:
        return (env("JVVOICE_API_KEY") or "").strip()

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

    def _resolved_jvvoice_base_url(self) -> str:
        return (
            (self.jvvoice_base_url or self._env_jvvoice_base_url()).strip().rstrip("/")
        )

    def _resolved_jvvoice_api_key(self) -> str:
        return (self.jvvoice_api_key or self._env_jvvoice_api_key()).strip()

    def _resolved_jvagent_base_url(self) -> str:
        return (
            (self.jvagent_base_url or self._env_jvagent_base_url()).strip().rstrip("/")
        )

    def is_configured(self) -> bool:
        """Return True when jvvoice delegation URL, API key, and agent name are set."""
        return bool(
            self._resolved_jvvoice_base_url()
            and self._resolved_jvvoice_api_key()
            and (self.agent_name or "").strip()
        )

    def get_capabilities(self) -> List[str]:
        """Return voice-call capabilities for PersonaAction when enabled."""
        if not self.enabled or not self.is_configured():
            return []
        return [
            "Answer inbound WhatsApp voice calls via jvvoice",
            "Conduct realtime voice conversations bridged to the agent Orchestrator",
        ]

    async def _get_whatsapp_action(self) -> Any:
        wa: Any = await self.get_action(self.whatsapp_action)
        if wa is None:
            raise ValueError(
                f"Sibling {self.whatsapp_action!r} not found on this agent"
            )
        if not getattr(wa, "is_meta_provider", lambda: False)():
            raise ValueError(
                f"{self.whatsapp_action} must use provider 'meta' for voice calls"
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

    async def _jvvoice_client(self) -> JvvoiceClient:
        if self._jvvoice is None:
            base_url = self._resolved_jvvoice_base_url()
            api_key = self._resolved_jvvoice_api_key()
            if not base_url or not api_key:
                raise JvvoiceClientError(
                    "jvvoice_base_url and jvvoice_api_key must be configured"
                )
            self._jvvoice = JvvoiceClient(base_url=base_url, api_key=api_key)
        return self._jvvoice

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

        base_url = self._resolved_jvagent_base_url()
        if not base_url:
            return {
                "status": "error",
                "call_id": event.call_id,
                "error": "jvagent_base_url is not configured (JVAGENT_PUBLIC_BASE_URL)",
            }

        payload = {
            "jvagent_agent_id": agent_id,
            "jvagent_base_url": base_url,
            "caller_phone": event.from_number,
            "caller_name": event.contact_name,
            "whatsapp_call_id": event.call_id,
            "phone_number_id": phone_number_id,
            "whatsapp_api_key": access_token,
            "cloud_api_version": self.cloud_api_version,
            "sdp": event.sdp,
            "sdp_type": event.sdp_type or "offer",
            "agent_name": self.agent_name,
        }

        try:
            client = await self._jvvoice_client()
            result = await client.accept_call(payload)
            if result.get("status") != "connected":
                return {
                    "status": "error",
                    "call_id": event.call_id,
                    "error": result.get("error") or "jvvoice accept failed",
                }
            room_name = str(result.get("room_name") or "").strip()
            self._active_calls[event.call_id] = room_name
            logger.info(
                "Delegated WhatsApp call accept call_id=%s room=%s agent=%s",
                event.call_id,
                room_name or "(none)",
                self.agent_name,
            )
            response: Dict[str, Any] = {
                "status": "connected",
                "call_id": event.call_id,
            }
            if room_name:
                response["room_name"] = room_name
            return response
        except (JvvoiceClientError, ValueError) as exc:
            logger.error(
                "Failed to delegate WhatsApp call accept call_id=%s: %s",
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
            client = await self._jvvoice_client()
            result = await client.disconnect_call(
                whatsapp_call_id=event.call_id,
                user_initiated=True,
            )
            logger.info(
                "Delegated WhatsApp disconnect call_id=%s (user initiated)",
                event.call_id,
            )
            response: Dict[str, Any] = {
                "status": "disconnected",
                "call_id": event.call_id,
            }
            if result.get("warning"):
                response["warning"] = result["warning"]
            return response
        except JvvoiceClientError as exc:
            logger.warning(
                "jvvoice disconnect failed for call_id=%s: %s",
                event.call_id,
                exc,
            )
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
            client = await self._jvvoice_client()
            await client.disconnect_call(
                whatsapp_call_id=call_id,
                whatsapp_api_key=access_token,
                user_initiated=False,
            )
            self._active_calls.pop(call_id, None)
            return {"ok": True, "call_id": call_id}
        except (JvvoiceClientError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "call_id": call_id}

    async def on_deregister(self) -> None:
        if self._jvvoice is not None:
            await self._jvvoice.close()
            self._jvvoice = None
        await super().on_deregister()
