"""WhatsApp Cloud API (Meta Graph API) provider — text-only MVP."""

import logging
import re
from typing import Any, Dict, Optional

from .base import BaseWhatsAppAPI, MessagePayload

logger = logging.getLogger(__name__)

META_TEXT_MAX_LENGTH = 4096


class MetaWhatsAppAPI(BaseWhatsAppAPI):
    """WhatsApp Business Cloud API via graph.facebook.com."""

    def __init__(
        self,
        api_url: str,
        session: str,
        token: str,
        secret_key: Optional[str] = None,
        timeout: float = 10.0,
        phone_number_id: str = "",
    ) -> None:
        super().__init__(api_url, session, token, secret_key, timeout)
        self.phone_number_id = (phone_number_id or session or "").strip()

    def _build_headers(self, headers: Optional[dict] = None) -> dict:
        if headers is None:
            headers = {}
        if "Authorization" not in headers:
            if not self.token:
                return {"error": "access_token required for authentication"}
            headers["Authorization"] = f"Bearer {self.token}"
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        return headers

    def _messages_url(self) -> str:
        return f"{self.api_url.rstrip('/')}/{self.phone_number_id}/messages"

    @staticmethod
    def _normalize_recipient(phone: str) -> str:
        """Strip bridge suffixes; Cloud API accepts digits with optional + prefix."""
        s = str(phone or "").strip()
        s = s.replace("@c.us", "").replace("@g.us", "").replace("@lid", "")
        if not s:
            return s
        if s.startswith("+"):
            return s
        digits = re.sub(r"\D", "", s)
        return digits or s

    async def send_rest_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        json_body: bool = True,
        use_full_url: bool = False,
    ) -> dict:
        headers = self._build_headers(headers)
        if "error" in headers:
            return {"ok": False, "error": headers["error"]}
        url = endpoint if use_full_url else f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        result = await self._make_request(url, method, headers, data, params, json_body)
        if result.get("ok", True) and "messaging_product" in result:
            result["ok"] = True
        elif result.get("error") and "ok" not in result:
            result["ok"] = False
        return result

    async def register_session(
        self,
        webhook_url: str = "",
        wait_qr_code: bool = True,
        auto_register: bool = True,
    ) -> dict:
        """Cloud API has no bridge session; webhook is configured in Meta App Dashboard."""
        return {
            "ok": True,
            "status": "skipped",
            "reason": "meta_cloud_api",
            "message": "Configure webhook in Meta App Dashboard (WhatsApp > Webhooks)",
        }

    async def convert_lid_to_phone_number(self, lid: str) -> str:
        return lid

    @staticmethod
    def _contact_name_map(value: dict) -> Dict[str, str]:
        names: Dict[str, str] = {}
        for contact in value.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            wa_id = str(contact.get("wa_id") or "").strip()
            profile = contact.get("profile") or {}
            name = (profile.get("name") or "").strip() if isinstance(profile, dict) else ""
            if wa_id and name:
                names[wa_id] = name
        return names

    @staticmethod
    def _extract_inbound_message(
        request: dict, expected_phone_number_id: str = ""
    ) -> Optional[tuple[dict, dict]]:
        """Return (message, change_value) for first inbound user message, or None."""
        if request.get("object") != "whatsapp_business_account":
            return None
        for entry in request.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                if not isinstance(change, dict):
                    continue
                if change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata") or {}
                phone_id = str(metadata.get("phone_number_id") or "").strip()
                if expected_phone_number_id and phone_id and phone_id != expected_phone_number_id:
                    logger.debug(
                        "Ignoring webhook for phone_number_id %s (expected %s)",
                        phone_id,
                        expected_phone_number_id,
                    )
                    continue
                messages = value.get("messages") or []
                if not messages:
                    continue
                msg = messages[0]
                if isinstance(msg, dict):
                    return msg, value
        return None

    async def parse_inbound_message(self, request: dict) -> Optional[MessagePayload]:
        """Parse Meta Cloud API webhook envelope into MessagePayload."""
        try:
            extracted = self._extract_inbound_message(
                request, expected_phone_number_id=self.phone_number_id
            )
            if not extracted:
                if request.get("object") == "whatsapp_business_account":
                    return MessagePayload(
                        message_id="",
                        event_type="meta_webhook",
                        message_type="ignored",
                        author="",
                        sender="",
                        receiver="",
                    )
                return None

            msg, value = extracted
            msg_type = str(msg.get("type") or "").lower()
            if msg_type != "text":
                return MessagePayload(
                    message_id=str(msg.get("id") or ""),
                    event_type="meta_webhook",
                    message_type="ignored",
                    author="",
                    sender=str(msg.get("from") or ""),
                    receiver="",
                )

            text_obj = msg.get("text") or {}
            body = (text_obj.get("body") or "").strip() if isinstance(text_obj, dict) else ""
            sender = str(msg.get("from") or "")
            names = self._contact_name_map(value)
            sender_name = names.get(sender, "")

            metadata = value.get("metadata") or {}
            display_phone = str(metadata.get("display_phone_number") or "")

            quoted: Dict[str, Any] = {}
            context = msg.get("context")
            if isinstance(context, dict) and context.get("id"):
                quoted = {"id": context.get("id")}

            return MessagePayload(
                message_id=str(msg.get("id") or ""),
                event_type="meta_webhook",
                message_type="chat",
                author=sender,
                sender=sender,
                receiver=display_phone,
                body=body,
                fromMe=False,
                isGroup=False,
                sender_name=sender_name,
                quoted_message=quoted,
            )
        except Exception as e:
            self.logger.error("Error parsing Meta inbound message: %s", e)
            return None

    async def set_typing_status(
        self, phone: str, value: bool = True, is_group: bool = False
    ) -> dict:
        if is_group:
            return {"ok": True, "skipped": True, "reason": "groups_not_supported"}
        if not value:
            return {"ok": True, "skipped": True, "reason": "typing_off_noop"}

        to = self._normalize_recipient(phone)
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "typing_indicator": {"type": "text"},
        }
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    async def mark_message_read(self, message_id: str) -> dict:
        if not message_id:
            return {"ok": True, "skipped": True}
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    async def send_message(
        self,
        phone: str,
        message: str,
        is_group: bool = False,
        is_newsletter: bool = False,
        message_id: str = "",
        options: Optional[dict] = None,
    ) -> dict:
        if is_group:
            return {"ok": False, "error": "Group messaging not supported for meta provider v1"}
        to = self._normalize_recipient(phone)
        text_body = (message or "")[:META_TEXT_MAX_LENGTH]
        data: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": True, "body": text_body},
        }
        if message_id:
            data["context"] = {"message_id": message_id}
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    async def send_image(self, phone: str, file_url: str = "", **kwargs) -> dict:
        return {"ok": False, "error": "Media not supported for meta provider v1"}

    async def send_file(self, phone: str, file_url: str = "", **kwargs) -> dict:
        return {"ok": False, "error": "Media not supported for meta provider v1"}

    async def send_video(self, phone: str, file_url: str = "", **kwargs) -> dict:
        return {"ok": False, "error": "Media not supported for meta provider v1"}

    async def send_voice(
        self, phone: str, file_url: str = "", is_ptt: bool = True, **kwargs
    ) -> dict:
        return {"ok": False, "error": "Voice not supported for meta provider v1"}

    async def send_location(
        self, phone: str, latitude: float = 0.0, longitude: float = 0.0, **kwargs
    ) -> dict:
        return {"ok": False, "error": "Location not supported for meta provider v1"}

    async def status(self) -> dict:
        return {"status": "CONNECTED", "provider": "meta", "ok": True}

    async def qrcode(self) -> dict:
        return {"ok": False, "error": "QR code not used with Cloud API provider"}

    async def get_host_device(self) -> dict:
        return {"provider": "meta", "phone_number_id": self.phone_number_id}

    async def get_profile_picture(self, phone: str = "", **kwargs) -> dict:
        return {"ok": False, "error": "Not implemented for meta provider v1"}

    async def logout_session(self) -> dict:
        return {
            "ok": True,
            "status": "not_applicable",
            "reason": "meta_cloud_api",
        }

    async def close_session(self) -> dict:
        return {
            "ok": True,
            "status": "not_applicable",
            "reason": "meta_cloud_api",
        }
