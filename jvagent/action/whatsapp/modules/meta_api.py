"""WhatsApp Cloud API (Meta Graph API) provider."""

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from .base import BaseWhatsAppAPI, MessagePayload, get_connection_pool

logger = logging.getLogger(__name__)

META_TEXT_MAX_LENGTH = 4096
META_CAPTION_MAX_LENGTH = 1024

# Meta webhook type -> jvagent message_type (audio resolved in parser)
_META_INBOUND_TYPES = frozenset(
    {"text", "image", "video", "document", "audio", "location"}
)
# Explicit non-user / unsupported types (logged and ignored)
_META_DENIED_TYPES = frozenset(
    {
        "system",
        "unsupported",
        "reaction",
        "request_welcome",
        "sticker",
        "button",
        "interactive",
    }
)


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
        waba_id: str = "",
        verify_token: str = "",
    ) -> None:
        super().__init__(api_url, session, token, secret_key, timeout)
        self.phone_number_id = (phone_number_id or session or "").strip()
        self.waba_id = (waba_id or "").strip()
        self.verify_token = (verify_token or "").strip()

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
        url = (
            endpoint
            if use_full_url
            else f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        )
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
        """Register webhook override with Meta (delegates to register_webhook_subscription)."""
        callback = self._strip_query(webhook_url)
        token = self.verify_token
        if not callback or not token:
            return {
                "ok": False,
                "status": "skipped",
                "reason": "webhook_url and verify_token required",
            }
        return await self.register_webhook_subscription(callback, token)

    @staticmethod
    def _strip_query(url: str) -> str:
        s = (url or "").strip()
        q = s.find("?")
        return s[:q] if q >= 0 else s

    async def _register_waba_webhook_override(self, callback: str, verify: str) -> dict:
        """POST WABA subscribed_apps override."""
        url = f"{self.api_url.rstrip('/')}/{self.waba_id}/subscribed_apps"
        override_data: Dict[str, Any] = {
            "override_callback_uri": callback,
            "verify_token": verify,
        }
        target = f"waba:{self.waba_id}"
        result = await self.send_rest_request(
            url, method="POST", data=override_data, use_full_url=True
        )
        if self._needs_waba_subscribe_first(result):
            logger.info(
                "Meta WABA not subscribed yet; subscribing before override (%s)",
                target,
            )
            subscribe = await self.send_rest_request(
                url, method="POST", data=None, use_full_url=True
            )
            if not self._graph_success(subscribe):
                return self._normalize_graph_result(subscribe, target, callback)
            result = await self.send_rest_request(
                url, method="POST", data=override_data, use_full_url=True
            )
        return self._normalize_graph_result(result, target, callback)

    async def _register_phone_webhook_override(
        self, callback: str, verify: str
    ) -> dict:
        """POST phone-number webhook_configuration override."""
        url = f"{self.api_url.rstrip('/')}/{self.phone_number_id}"
        data = {
            "webhook_configuration": {
                "override_callback_uri": callback,
                "verify_token": verify,
            }
        }
        target = f"phone:{self.phone_number_id}"
        result = await self.send_rest_request(
            url, method="POST", data=data, use_full_url=True
        )
        return self._normalize_graph_result(result, target, callback)

    async def register_webhook_subscription(
        self, callback_url: str, verify_token: str
    ) -> dict:
        """Set WABA and/or phone-number webhook overrides via Graph API."""
        callback = self._strip_query(callback_url)
        verify = (verify_token or self.verify_token or "").strip()
        if not callback:
            return {"ok": False, "error": "callback_url is required"}
        if not verify:
            return {"ok": False, "error": "verify_token is required"}
        if not self.waba_id and not self.phone_number_id:
            return {
                "ok": False,
                "error": "waba_id or phone_number_id required on WhatsApp action",
            }

        registrations: Dict[str, dict] = {}
        if self.waba_id:
            registrations["waba"] = await self._register_waba_webhook_override(
                callback, verify
            )
        if self.phone_number_id:
            registrations["phone"] = await self._register_phone_webhook_override(
                callback, verify
            )

        any_ok = any(self._graph_success(r) for r in registrations.values())
        all_failed = all(not self._graph_success(r) for r in registrations.values())
        combined: Dict[str, Any] = {
            "ok": any_ok,
            "registrations": registrations,
        }
        if all_failed:
            errors = [
                str(r.get("error") or r)
                for r in registrations.values()
                if r.get("error")
            ]
            combined["error"] = "; ".join(errors) if errors else "Graph request failed"
        elif not any_ok:
            combined["error"] = "Graph request failed"
        return combined

    @staticmethod
    def _graph_error_message(result: dict) -> str:
        err = result.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err or "")

    @classmethod
    def _needs_waba_subscribe_first(cls, result: dict) -> bool:
        if cls._graph_success(result):
            return False
        msg = cls._graph_error_message(result).lower()
        return "before override" in msg or "(#100)" in msg

    @staticmethod
    def _graph_success(result: dict) -> bool:
        if result.get("success") is True:
            return True
        if result.get("ok") is True:
            return True
        if result.get("ok") is False or result.get("error"):
            return False
        return "error" not in result

    def _normalize_graph_result(self, result: dict, target: str, callback: str) -> dict:
        if self._graph_success(result):
            result["ok"] = True
            logger.info(
                "Meta webhook override registered for %s -> %s", target, callback
            )
            return result
        msg = self._graph_error_message(result)
        if msg:
            result["ok"] = False
            result["error"] = msg
        else:
            result["ok"] = False
            result["error"] = result or "Graph request failed"
        return result

    async def get_webhook_override_status(self) -> dict:
        """Fetch WABA and/or phone-number webhook configuration from Meta."""
        if not self.waba_id and not self.phone_number_id:
            return {
                "ok": False,
                "error": "waba_id or phone_number_id required on WhatsApp action",
            }

        out: Dict[str, Any] = {"ok": True}
        if self.waba_id:
            url = f"{self.api_url.rstrip('/')}/{self.waba_id}/subscribed_apps"
            waba = await self.send_rest_request(url, method="GET", use_full_url=True)
            out["waba"] = waba
            if not self._graph_success(waba):
                out["ok"] = False
        if self.phone_number_id:
            url = (
                f"{self.api_url.rstrip('/')}/{self.phone_number_id}"
                "?fields=webhook_configuration"
            )
            phone = await self.send_rest_request(url, method="GET", use_full_url=True)
            out["phone"] = phone
            if not self._graph_success(phone):
                out["ok"] = False
        return out

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
            name = (
                (profile.get("name") or "").strip() if isinstance(profile, dict) else ""
            )
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
                if (
                    expected_phone_number_id
                    and phone_id
                    and phone_id != expected_phone_number_id
                ):
                    logger.debug(
                        "Ignoring webhook for phone_number_id %s (expected %s)",
                        phone_id,
                        expected_phone_number_id,
                    )
                    continue
                messages = value.get("messages") or []
                if not messages:
                    statuses = value.get("statuses") or []
                    if statuses:
                        logger.debug(
                            "Meta webhook statuses-only (sent/delivered/read); skipping"
                        )
                    continue
                msg = messages[0]
                if isinstance(msg, dict):
                    return msg, value
        return None

    async def download_media(self, media_id: str) -> Tuple[bytes, str]:
        """Fetch media bytes from Meta Graph (media id from webhook)."""
        mid = (media_id or "").strip()
        if not mid:
            return b"", ""
        url = f"{self.api_url.rstrip('/')}/{mid}"
        meta = await self.send_rest_request(url, method="GET", use_full_url=True)
        if meta.get("error") or not meta.get("url"):
            logger.warning("Meta media metadata fetch failed for %s: %s", mid, meta)
            return b"", ""
        download_url = str(meta["url"])
        mime = str(meta.get("mime_type") or "application/octet-stream")
        headers = self._build_headers()
        if "error" in headers:
            return b"", ""
        headers.pop("Content-Type", None)
        result = await self._make_request(download_url, "GET", headers, json_body=False)
        raw = result.get("raw")
        if isinstance(raw, bytes) and raw:
            return raw, mime
        logger.warning("Meta media download empty for %s", mid)
        return b"", mime

    async def _fetch_url_bytes(self, file_url: str) -> Tuple[bytes, str]:
        """Download file from URL (jvagent public file URL) for outbound upload."""
        encoded = await self.file_url_to_base64(file_url, force_prefix=True)
        if not encoded:
            return b"", ""
        if "," in encoded:
            header, payload = encoded.split(",", 1)
            mime = "application/octet-stream"
            if header.startswith("data:"):
                mime = header[5:].split(";")[0].strip() or mime
            try:
                return base64.b64decode(payload), mime
            except (ValueError, TypeError):
                return b"", ""
        try:
            return base64.b64decode(encoded), "application/octet-stream"
        except (ValueError, TypeError):
            return b"", ""

    async def _upload_media(
        self, file_bytes: bytes, mime_type: str, filename: str = "file"
    ) -> str:
        """Upload bytes to Meta Media API; return media id."""
        if not file_bytes:
            return ""
        url = f"{self.api_url.rstrip('/')}/{self.phone_number_id}/media"
        clean_mime = (mime_type or "application/octet-stream").split(";")[0].strip()
        form = aiohttp.FormData()
        form.add_field("messaging_product", "whatsapp")
        form.add_field(
            "file",
            file_bytes,
            filename=filename,
            content_type=clean_mime,
        )
        headers: Dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        pool = await get_connection_pool()
        session = await pool.get_session(self.api_url, self.timeout)
        async with session.post(url, data=form, headers=headers) as resp:
            body = await resp.read()
            if resp.status >= 400:
                logger.warning(
                    "Meta media upload failed HTTP %s: %s",
                    resp.status,
                    body[:500],
                )
                return ""
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return ""
            return str(parsed.get("id") or "")

    async def _send_media_message(
        self,
        phone: str,
        msg_type: str,
        media_id: str,
        caption: str = "",
        context_id: str = "",
        extra_media_fields: Optional[Dict[str, Any]] = None,
    ) -> dict:
        if not media_id:
            return {"ok": False, "error": "media_id required"}
        to = self._normalize_recipient(phone)
        media_obj: Dict[str, Any] = {"id": media_id}
        if caption and msg_type in ("image", "video", "document"):
            media_obj["caption"] = caption[:META_CAPTION_MAX_LENGTH]
        if extra_media_fields:
            media_obj.update(extra_media_fields)
        data: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": msg_type,
            msg_type: media_obj,
        }
        if context_id:
            data["context"] = {"message_id": context_id}
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    @staticmethod
    def _jvagent_message_type(msg: dict) -> str:
        """Map Meta message type to jvagent MessagePayload.message_type."""
        msg_type = str(msg.get("type") or "").lower()
        if msg_type == "text":
            return "chat"
        if msg_type == "audio":
            audio = msg.get("audio") or {}
            if isinstance(audio, dict) and audio.get("voice"):
                return "ptt"
            return "audio"
        if msg_type in ("image", "video", "document"):
            return msg_type
        if msg_type == "location":
            return "location"
        return "ignored"

    @staticmethod
    def _meta_type_payload(msg: dict) -> dict:
        msg_type = str(msg.get("type") or "").lower()
        payload = msg.get(msg_type)
        return payload if isinstance(payload, dict) else {}

    async def _populate_inbound_media(self, msg: dict, payload: MessagePayload) -> None:
        msg_type = str(msg.get("type") or "").lower()
        if msg_type not in ("image", "video", "document", "audio"):
            return
        media_obj = self._meta_type_payload(msg)
        media_id = str(media_obj.get("id") or "").strip()
        if not media_id:
            return
        file_bytes, mime = await self.download_media(media_id)
        if file_bytes:
            payload.media = base64.b64encode(file_bytes).decode("ascii")
        payload.mime_type = str(
            media_obj.get("mime_type") or mime or payload.mime_type or ""
        )
        if msg_type == "document":
            payload.filename = str(media_obj.get("filename") or "")
        caption = media_obj.get("caption")
        if caption and not payload.body:
            payload.body = str(caption)
            payload.caption = str(caption)

    @staticmethod
    def _webhook_has_statuses_only(request: dict) -> bool:
        """True when the envelope is delivery/read/sent with no user messages."""
        if request.get("object") != "whatsapp_business_account":
            return False
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
                statuses = value.get("statuses") or []
                messages = value.get("messages") or []
                if statuses and not messages:
                    return True
        return False

    async def parse_inbound_message(self, request: dict) -> Optional[MessagePayload]:
        """Parse Meta Cloud API webhook envelope into MessagePayload."""
        try:
            if self._webhook_has_statuses_only(request):
                logger.debug(
                    "Meta webhook statuses-only (delivery/read/sent); ignoring"
                )
                return MessagePayload(
                    message_id="",
                    event_type="meta_webhook",
                    message_type="ignored",
                    author="",
                    sender="",
                    receiver="",
                )

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
            sender = str(msg.get("from") or "")
            names = self._contact_name_map(value)
            sender_name = names.get(sender, "")
            metadata = value.get("metadata") or {}
            display_phone = str(metadata.get("display_phone_number") or "")

            quoted: Dict[str, Any] = {}
            context = msg.get("context")
            if isinstance(context, dict) and context.get("id"):
                quoted = {"id": context.get("id")}

            jv_type = self._jvagent_message_type(msg)
            if msg_type in _META_DENIED_TYPES or msg_type not in _META_INBOUND_TYPES:
                logger.debug(
                    "Meta inbound type %r ignored (non-user or unsupported message)",
                    msg_type,
                )
                return MessagePayload(
                    message_id=str(msg.get("id") or ""),
                    event_type="meta_webhook",
                    message_type="ignored",
                    author=sender,
                    sender=sender,
                    receiver=display_phone,
                )

            body = ""
            caption = ""
            location: Dict[str, Any] = {}
            if msg_type == "text":
                text_obj = msg.get("text") or {}
                body = (
                    (text_obj.get("body") or "").strip()
                    if isinstance(text_obj, dict)
                    else ""
                )
            elif msg_type == "location":
                loc = msg.get("location") or {}
                if isinstance(loc, dict):
                    location = {
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude"),
                        "name": loc.get("name", ""),
                        "address": loc.get("address", ""),
                    }

            payload = MessagePayload(
                message_id=str(msg.get("id") or ""),
                event_type="meta_webhook",
                message_type=jv_type,
                author=sender,
                sender=sender,
                receiver=display_phone,
                body=body,
                caption=caption,
                location=location,
                fromMe=False,
                isGroup=False,
                sender_name=sender_name,
                quoted_message=quoted,
            )

            if msg_type in ("image", "video", "document", "audio"):
                await self._populate_inbound_media(msg, payload)
                if not payload.media:
                    logger.warning(
                        "Meta inbound %s missing downloadable media; ignoring",
                        msg_type,
                    )
                    payload.message_type = "ignored"

            return payload
        except Exception as e:
            self.logger.error("Error parsing Meta inbound message: %s", e)
            return None

    async def set_typing_status(
        self,
        phone: str,
        value: bool = True,
        is_group: bool = False,
        message_id: str = "",
    ) -> dict:
        if is_group:
            return {"ok": True, "skipped": True, "reason": "groups_not_supported"}
        if not value:
            return {"ok": True, "skipped": True, "reason": "typing_off_noop"}

        wamid = (message_id or "").strip()
        if wamid:
            data: Dict[str, Any] = {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": wamid,
                "typing_indicator": {"type": "text"},
            }
            return await self.send_rest_request(
                self._messages_url(), method="POST", data=data, use_full_url=True
            )

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

    async def set_recording_status(
        self,
        phone: str,
        value: bool = True,
        is_group: bool = False,
        duration: int = 5,
    ) -> dict:
        """Cloud API has no recording indicator; typing is used instead."""
        return {"ok": True, "skipped": True, "reason": "meta_cloud_api"}

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
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
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

    async def list_message_templates(self) -> dict:
        """List sendable Meta message templates for the configured WABA.

        Direct Graph access (non-jvconnect). Bridge providers should not call this.
        """
        waba = (self.waba_id or "").strip()
        if not waba:
            return {"ok": False, "error": "waba_id required to list templates"}
        url = (
            f"{self.api_url.rstrip('/')}/{waba}/message_templates"
            f"?fields=name,language,status,components,category&limit=1000"
        )
        data = await self.send_rest_request(url, method="GET", use_full_url=True)
        if data.get("error") and not data.get("ok", True):
            return {"ok": False, "error": data.get("error"), "raw": data}
        templates = data.get("data") or []
        if not isinstance(templates, list):
            templates = []
        sendable = {"APPROVED", "QUALITY_PENDING"}
        filtered = [
            t
            for t in templates
            if isinstance(t, dict) and str(t.get("status") or "") in sendable
        ]
        return {"ok": True, "templates": filtered}

    async def send_template_message(
        self,
        phone: str,
        template_name: str,
        language: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """Send an approved Meta template (HSM) to *phone*."""
        to = self._normalize_recipient(phone)
        name = (template_name or "").strip()
        if not to or not name:
            return {"ok": False, "error": "phone and template_name are required"}
        lang = (language or "en_US").strip() or "en_US"
        data: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": lang},
                "components": list(components or []),
            },
        }
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    async def list_flows(self) -> dict:
        """List WhatsApp Flows for the configured WABA (direct Graph)."""
        waba = (self.waba_id or "").strip()
        if not waba:
            return {"ok": False, "error": "waba_id required to list flows"}
        url = (
            f"{self.api_url.rstrip('/')}/{waba}/flows"
            "?fields=id,name,status,categories,endpoint_uri&limit=100"
        )
        data = await self.send_rest_request(url, method="GET", use_full_url=True)
        if data.get("error") and not data.get("ok", True):
            return {"ok": False, "error": data.get("error"), "raw": data}
        flows = data.get("data") or []
        if not isinstance(flows, list):
            flows = []
        return {"ok": True, "flows": [f for f in flows if isinstance(f, dict)]}

    async def send_flow_message(
        self,
        phone: str,
        *,
        flow_id: str = "",
        flow_name: str = "",
        flow_cta: str = "Open",
        body: str = "Please complete this form.",
        flow_token: str = "",
        flow_action: str = "",
        screen: str = "",
        flow_action_data: Optional[Dict[str, Any]] = None,
        mode: str = "",
        header: str = "",
        footer: str = "",
    ) -> dict:
        """Send an interactive WhatsApp Flow message to *phone*."""
        to = self._normalize_recipient(phone)
        fid = str(flow_id or "").strip()
        fname = (flow_name or "").strip()
        if not to or (not fid and not fname):
            return {"ok": False, "error": "phone and flow_id or flow_name are required"}
        cta = (flow_cta or "Open").strip() or "Open"
        text = (body or "").strip() or "Please complete this form."
        action_params: Dict[str, Any] = {
            "flow_message_version": "3",
            "flow_cta": cta,
        }
        if fid:
            action_params["flow_id"] = fid
        if fname:
            action_params["flow_name"] = fname
        if flow_token:
            action_params["flow_token"] = flow_token
        if mode:
            action_params["mode"] = mode

        action = (flow_action or "").strip().lower()
        screen_id = (screen or "").strip()
        if action == "data_exchange":
            action_params["flow_action"] = "data_exchange"
        elif screen_id:
            action_params["flow_action"] = "navigate"
            payload: Dict[str, Any] = {"screen": screen_id}
            if flow_action_data:
                payload["data"] = flow_action_data
            action_params["flow_action_payload"] = payload

        interactive: Dict[str, Any] = {
            "type": "flow",
            "body": {"text": text},
            "action": {"name": "flow", "parameters": action_params},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        if footer:
            interactive["footer"] = {"text": footer}

        data: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )

    async def send_image(
        self,
        phone: str,
        file_url: str = "",
        caption: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        if is_group:
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
        file_bytes, mime = await self._fetch_url_bytes(file_url)
        if not file_bytes:
            return {"ok": False, "error": "Failed to fetch image from URL"}
        media_id = await self._upload_media(
            file_bytes, mime or "image/jpeg", "image.jpg"
        )
        if not media_id:
            return {"ok": False, "error": "Meta media upload failed"}
        result = await self._send_media_message(
            phone, "image", media_id, caption=caption
        )
        if result.get("ok", True) and "error" not in result:
            result["ok"] = True
        return result

    async def send_file(
        self,
        phone: str,
        file_url: str = "",
        caption: str = "",
        filename: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        if is_group:
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
        file_bytes, mime = await self._fetch_url_bytes(file_url)
        if not file_bytes:
            return {"ok": False, "error": "Failed to fetch document from URL"}
        fname = filename or "document"
        media_id = await self._upload_media(
            file_bytes, mime or "application/octet-stream", fname
        )
        if not media_id:
            return {"ok": False, "error": "Meta media upload failed"}
        result = await self._send_media_message(
            phone, "document", media_id, caption=caption
        )
        if result.get("ok", True) and "error" not in result:
            result["ok"] = True
        return result

    async def send_video(
        self,
        phone: str,
        file_url: str = "",
        caption: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        if is_group:
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
        file_bytes, mime = await self._fetch_url_bytes(file_url)
        if not file_bytes:
            return {"ok": False, "error": "Failed to fetch video from URL"}
        media_id = await self._upload_media(
            file_bytes, mime or "video/mp4", "video.mp4"
        )
        if not media_id:
            return {"ok": False, "error": "Meta media upload failed"}
        result = await self._send_media_message(
            phone, "video", media_id, caption=caption
        )
        if result.get("ok", True) and "error" not in result:
            result["ok"] = True
        return result

    async def send_voice(
        self,
        phone: str,
        file_url: str = "",
        is_ptt: bool = True,
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        if is_group:
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
        file_bytes, mime = await self._fetch_url_bytes(file_url)
        if not file_bytes:
            return {"ok": False, "error": "Failed to fetch audio from URL"}
        clean_mime = (mime or "audio/mpeg").split(";")[0].strip().lower()
        is_voice_note = is_ptt and ("ogg" in clean_mime or "opus" in clean_mime)
        if (
            is_ptt
            and not is_voice_note
            and clean_mime
            in (
                "audio/mpeg",
                "audio/mp3",
                "audio/x-mpeg",
            )
        ):
            from ..utils.meta_audio import transcode_mp3_to_ogg_opus

            ogg_bytes = transcode_mp3_to_ogg_opus(file_bytes)
            if ogg_bytes:
                file_bytes = ogg_bytes
                clean_mime = "audio/ogg"
                is_voice_note = True
        ext = "voice.ogg" if is_voice_note else "audio.mp3"
        upload_mime = clean_mime if clean_mime else "audio/mpeg"
        media_id = await self._upload_media(file_bytes, upload_mime, ext)
        if not media_id:
            return {"ok": False, "error": "Meta media upload failed"}
        extra: Dict[str, Any] = {}
        if is_voice_note:
            extra["voice"] = True
        result = await self._send_media_message(
            phone, "audio", media_id, extra_media_fields=extra
        )
        if result.get("ok", True) and "error" not in result:
            result["ok"] = True
        return result

    async def send_location(
        self,
        phone: str,
        latitude: float = 0.0,
        longitude: float = 0.0,
        title: str = "",
        is_group: bool = False,
        **kwargs,
    ) -> dict:
        if is_group:
            return {
                "ok": False,
                "error": "Group messaging not supported for meta provider v1",
            }
        to = self._normalize_recipient(phone)
        location_obj: Dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
        }
        if title:
            location_obj["name"] = str(title)[:1024]
        data: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "location",
            "location": location_obj,
        }
        result = await self.send_rest_request(
            self._messages_url(), method="POST", data=data, use_full_url=True
        )
        if result.get("ok", True) and "error" not in result:
            result["ok"] = True
        return result

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
