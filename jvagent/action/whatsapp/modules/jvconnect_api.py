"""WhatsApp Cloud API via jvconnect Messaging API (credential proxy).

Keeps Meta access tokens and app secrets on jvconnect. jvagent authenticates
with ``JVCONNECT_API_KEY`` and calls purpose-built ``/api/v1/whatsapp/*`` routes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple
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

    def _v1(self, path: str) -> str:
        return f"{self._jvconnect_base}/api/v1/whatsapp/{path.lstrip('/')}"

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
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
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
                data = {"ok": False, "error": body[:500].decode("utf-8", errors="replace")}
            if not isinstance(data, dict):
                data = {"ok": False, "error": "invalid response", "raw": data}
            if resp.status >= 400 and "error" not in data:
                data["ok"] = False
                data["error"] = data.get("message") or f"HTTP {resp.status}"
            elif "ok" not in data and "error" not in data:
                data["ok"] = True
            return data

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
        """Route message POSTs through jvconnect; other Graph paths are not used."""
        url = endpoint if use_full_url else f"{self.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        if "/messages" in url and method.upper() == "POST":
            payload = {
                "phone_number_id": self.phone_number_id,
                "message": data or {},
            }
            if self.waba_id:
                payload["waba_id"] = self.waba_id
            result = await self._jvconnect_json("POST", "messages", json_body=payload)
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
        params = {"phone_number_id": self.phone_number_id, "download": "1"}
        if self.waba_id:
            params["waba_id"] = self.waba_id
        url = self._v1(f"media/{mid}") + "?" + urlencode(params)
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
        form.add_field("phone_number_id", self.phone_number_id)
        if self.waba_id:
            form.add_field("waba_id", self.waba_id)
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
        if not self.phone_number_id:
            return {"ok": False, "error": "phone_number_id required"}

        payload: Dict[str, Any] = {
            "phone_number_id": self.phone_number_id,
            "callback_url": callback,
        }
        if self.waba_id:
            payload["waba_id"] = self.waba_id

        result = await self._jvconnect_json(
            "POST", "webhook/register", json_body=payload
        )
        if result.get("webhook_secret"):
            # Expose for WhatsAppAction to persist
            result["ok"] = True
            logger.info(
                "jvconnect webhook registered phone=%s -> %s",
                self.phone_number_id,
                callback,
            )
        elif result.get("error"):
            result["ok"] = False
        return result

    async def get_webhook_override_status(self) -> dict:
        if not self.phone_number_id:
            return {
                "ok": False,
                "error": "phone_number_id required on WhatsApp action",
            }
        params: Dict[str, str] = {"phone_number_id": self.phone_number_id}
        if self.waba_id:
            params["waba_id"] = self.waba_id
        return await self._jvconnect_json("GET", "webhook/register", params=params)
