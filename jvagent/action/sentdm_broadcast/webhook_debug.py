"""Inbound SentDM webhook request tracing (debug / operations).

Registers outer HTTP middleware so logs are emitted **before** jvspatial webhook
API-key authentication. That way missing query keys, proxy stripping, invalid
keys, or allowlist rejections still produce a correlatable log line.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jvspatial.api import Server

logger = logging.getLogger(__name__)

_SENTDM_PATH_MARK = "sentdm/webhook"


def register_sentdm_webhook_debug_middleware(server: Server) -> None:
    """Attach HTTP middleware that logs safe per-request diagnostics for SentDM webhooks."""

    @server.middleware("http")
    async def sentdm_webhook_request_trace(request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path or ""
        if _SENTDM_PATH_MARK not in path.replace("\\", "/"):
            return await call_next(request)

        query = request.query_params
        has_api_key_query = bool(query.get("api_key"))
        has_x_api_key_header = bool(request.headers.get("x-api-key"))
        has_signature = bool(request.headers.get("x-webhook-signature"))
        has_webhook_id = bool(request.headers.get("x-webhook-id"))
        content_length = request.headers.get("content-length")
        client_host = request.client.host if request.client else None
        x_forwarded_for = request.headers.get("x-forwarded-for")

        logger.info(
            "SentDM webhook inbound (pre jvspatial auth): path=%s "
            "api_key_in_query=%s x_api_key_header=%s "
            "x_webhook_signature=%s x_webhook_id=%s content_length=%s "
            "client_host=%s x_forwarded_for=%s",
            path,
            has_api_key_query,
            has_x_api_key_header,
            "present" if has_signature else "absent",
            "present" if has_webhook_id else "absent",
            content_length,
            client_host,
            x_forwarded_for,
        )

        response = await call_next(request)
        if response.status_code >= 400:
            logger.warning(
                "SentDM webhook response: path=%s status_code=%s",
                path,
                response.status_code,
            )
        return response
