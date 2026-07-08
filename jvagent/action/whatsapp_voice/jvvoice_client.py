"""HTTP client for the jvvoice connector delegation API."""

from __future__ import annotations

from typing import Any, Dict

import httpx


class JvvoiceClientError(Exception):
    """Raised when a jvvoice connector API call fails."""


class JvvoiceClient:
    """Delegate WhatsApp call accept/disconnect to a jvvoice deployment."""

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._api_key = (api_key or "").strip()
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def accept_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /api/calls/accept on jvvoice."""
        if not self._base_url or not self._api_key:
            raise JvvoiceClientError(
                "jvvoice_base_url and jvvoice_api_key are required"
            )
        url = f"{self._base_url}/api/calls/accept"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise JvvoiceClientError(
                f"jvvoice accept failed ({exc.response.status_code}): {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise JvvoiceClientError(f"jvvoice accept request failed: {exc}") from exc
        if not isinstance(body, dict):
            raise JvvoiceClientError("jvvoice accept returned non-JSON response")
        return body

    async def disconnect_call(
        self,
        *,
        whatsapp_call_id: str,
        user_initiated: bool = True,
        whatsapp_api_key: str = "",
    ) -> Dict[str, Any]:
        """POST /api/calls/disconnect on jvvoice."""
        if not self._base_url or not self._api_key:
            raise JvvoiceClientError(
                "jvvoice_base_url and jvvoice_api_key are required"
            )
        url = f"{self._base_url}/api/calls/disconnect"
        payload = {
            "whatsapp_call_id": whatsapp_call_id,
            "user_initiated": user_initiated,
            "whatsapp_api_key": whatsapp_api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise JvvoiceClientError(
                f"jvvoice disconnect failed ({exc.response.status_code}): {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise JvvoiceClientError(
                f"jvvoice disconnect request failed: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise JvvoiceClientError("jvvoice disconnect returned non-JSON response")
        return body

    async def close(self) -> None:
        """No-op; client uses short-lived HTTP sessions."""
