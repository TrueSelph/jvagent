"""WhatsApp Cloud API via jvconnect Messaging API (credential proxy).

Keeps Meta access tokens and app secrets on jvconnect. jvagent authenticates
with ``JVCONNECT_API_KEY``; the phone number is resolved from the key on jvconnect
(``GET /api/v1/meta/whatsapp/account``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from .base import get_connection_pool
from .meta_api import MetaWhatsAppAPI

logger = logging.getLogger(__name__)


class JvconnectWhatsAppAPI(MetaWhatsAppAPI):
    """Meta Cloud API shapes, routed through jvconnect instead of graph.facebook.com."""

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
        # api_url = jvconnect base (e.g. https://connect.example.com)
        # token = JVCONNECT_API_KEY
        # secret_key = jvconnect-issued webhook HMAC secret
        super().__init__(
            api_url=api_url,
            session=session,
            token=token,
            secret_key=secret_key,
            timeout=timeout,
            phone_number_id=phone_number_id,
            waba_id=waba_id,
            verify_token=verify_token,
        )
        self._jvconnect_base = (api_url or "").rstrip("/")
        self._account_loaded = False

    def _v1(self, path: str) -> str:
        return f"{self._jvconnect_base}/api/v1/meta/whatsapp/{path.lstrip('/')}"

    def _auth_headers(self, content_type: Optional[str] = "application/json") -> dict:
        headers: Dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _jvconnect_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = self._v1(path)
        if params:
            url = (
                f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
            )
        headers = self._auth_headers()
        pool = await get_connection_pool()
        session = await pool.get_session(self._jvconnect_base, self.timeout)
        async with session.request(
            method, url, headers=headers, json=json_body
        ) as resp:
            body = await resp.read()
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = {
                    "ok": False,
                    "error": body[:500].decode("utf-8", errors="replace"),
                }
            if not isinstance(data, dict):
                data = {"ok": False, "error": "invalid response", "raw": data}
            if resp.status >= 400 and "error" not in data:
                data["ok"] = False
                data["error"] = data.get("message") or f"HTTP {resp.status}"
            elif "ok" not in data and "error" not in data:
                data["ok"] = True
            return data

    async def fetch_account(self) -> dict:
        """Load phone_number_id / waba_id bound to this API key."""
        data = await self._jvconnect_json("GET", "account")
        if data.get("error"):
            data["ok"] = False
            return data
        phone = str(data.get("phone_number_id") or "").strip()
        waba = str(data.get("waba_id") or "").strip()
        if phone:
            self.phone_number_id = phone
            self.session = phone
        if waba:
            self.waba_id = waba
        self._account_loaded = True
        data["ok"] = True
        return data

    async def fetch_calling_credentials(self) -> dict:
        """Load Meta phone_number_id + access_token for LiveKit AcceptWhatsAppCall.

        Tokens stay on jvconnect; this endpoint mints them for the phone-bound
        API key so voice workers can talk to Meta Graph directly.
        """
        data = await self._jvconnect_json("GET", "calling/credentials")
        if data.get("error"):
            data["ok"] = False
            return data
        phone = str(data.get("phone_number_id") or "").strip()
        token = str(data.get("access_token") or "").strip()
        waba = str(data.get("waba_id") or "").strip()
        if phone:
            self.phone_number_id = phone
            self.session = phone
        if waba:
            self.waba_id = waba
        if not phone or not token:
            return {
                "ok": False,
                "error": "calling/credentials missing phone_number_id or access_token",
                "raw": data,
            }
        data["ok"] = True
        data["phone_number_id"] = phone
        data["access_token"] = token
        return data

    async def ensure_account(self) -> None:
        if not self._account_loaded or not self.phone_number_id:
            await self.fetch_account()

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
        """Route message POSTs through jvconnect; phone is resolved from the API key."""
        url = (
            endpoint
            if use_full_url
            else f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        )
        if "/messages" in url and method.upper() == "POST":
            result = await self._jvconnect_json(
                "POST", "messages", json_body={"message": data or {}}
            )
            if result.get("ok", True) and "messaging_product" in result:
                result["ok"] = True
            elif result.get("error") and "ok" not in result:
                result["ok"] = False
            return result
        logger.warning(
            "JvconnectWhatsAppAPI ignoring unsupported Graph path %s %s",
            method,
            url,
        )
        return {"ok": False, "error": f"unsupported via jvconnect: {method} {url}"}

    async def download_media(self, media_id: str) -> Tuple[bytes, str]:
        mid = (media_id or "").strip()
        if not mid:
            return b"", ""
        url = self._v1(f"media/{mid}") + "?download=1"
        headers = self._auth_headers(content_type=None)
        pool = await get_connection_pool()
        session = await pool.get_session(self._jvconnect_base, self.timeout)
        async with session.get(url, headers=headers) as resp:
            raw = await resp.read()
            if resp.status >= 400:
                logger.warning(
                    "jvconnect media download failed HTTP %s for %s", resp.status, mid
                )
                return b"", ""
            mime = resp.headers.get("Content-Type") or "application/octet-stream"
            return raw, mime.split(";")[0].strip()

    async def _upload_media(
        self, file_bytes: bytes, mime_type: str, filename: str = "file"
    ) -> str:
        if not file_bytes:
            return ""
        url = self._v1("media")
        clean_mime = (mime_type or "application/octet-stream").split(";")[0].strip()
        form = aiohttp.FormData()
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
        session = await pool.get_session(self._jvconnect_base, self.timeout)
        async with session.post(url, data=form, headers=headers) as resp:
            body = await resp.read()
            if resp.status >= 400:
                logger.warning(
                    "jvconnect media upload failed HTTP %s: %s",
                    resp.status,
                    body[:500],
                )
                return ""
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return ""
            return str(parsed.get("id") or "")

    async def register_webhook_subscription(
        self, callback_url: str, verify_token: str
    ) -> dict:
        """Register agent callback with jvconnect; Meta points at jvconnect."""
        callback = self._strip_query(callback_url)
        if not callback:
            return {"ok": False, "error": "callback_url is required"}

        result = await self._jvconnect_json(
            "POST", "webhook/register", json_body={"callback_url": callback}
        )
        if result.get("phone_number_id"):
            self.phone_number_id = str(result["phone_number_id"])
            self.session = self.phone_number_id
        if result.get("waba_id"):
            self.waba_id = str(result["waba_id"])
        if result.get("webhook_secret"):
            result["ok"] = True
            logger.info(
                "jvconnect webhook registered phone=%s -> %s",
                self.phone_number_id or "(from key)",
                callback,
            )
        elif result.get("error"):
            result["ok"] = False
        return result

    async def get_webhook_override_status(self) -> dict:
        return await self._jvconnect_json("GET", "webhook/register")

    async def list_message_templates(self) -> dict:
        """List sendable templates via jvconnect (key-bound WABA)."""
        await self.ensure_account()
        data = await self._jvconnect_json("GET", "templates")
        if data.get("error") and not data.get("ok", True):
            return {"ok": False, "error": data.get("error"), "raw": data}
        templates = data.get("templates") or data.get("data") or []
        if not isinstance(templates, list):
            templates = []
        return {
            "ok": True,
            "templates": templates,
            "phone_number_id": data.get("phone_number_id") or self.phone_number_id,
            "waba_id": data.get("waba_id") or self.waba_id,
        }

    async def send_template_message(
        self,
        phone: str,
        template_name: str,
        language: str = "en_US",
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """Send a Meta template through jvconnect ``POST .../messages``."""
        await self.ensure_account()
        return await MetaWhatsAppAPI.send_template_message(
            self, phone, template_name, language=language, components=components
        )

    async def list_flows(self) -> dict:
        """List WhatsApp Flows via jvconnect (key-bound WABA)."""
        await self.ensure_account()
        data = await self._jvconnect_json("GET", "flows")
        if data.get("error") and not data.get("ok", True):
            return {"ok": False, "error": data.get("error"), "raw": data}
        flows = data.get("flows") or data.get("data") or []
        if not isinstance(flows, list):
            flows = []
        return {
            "ok": True,
            "flows": flows,
            "phone_number_id": data.get("phone_number_id") or self.phone_number_id,
            "waba_id": data.get("waba_id") or self.waba_id,
        }

    async def send_flow_message(self, phone: str, **kwargs: Any) -> dict:
        """Send an interactive Flow through jvconnect ``POST .../messages``."""
        await self.ensure_account()
        return await MetaWhatsAppAPI.send_flow_message(self, phone, **kwargs)

    async def send_cta_url_message(self, phone: str, **kwargs: Any) -> dict:
        """Send an interactive CTA URL button through jvconnect ``POST .../messages``."""
        await self.ensure_account()
        return await MetaWhatsAppAPI.send_cta_url_message(self, phone, **kwargs)
