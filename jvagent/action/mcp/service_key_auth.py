"""httpx auth flow that signs MCP requests with Core's service-key envelope.

Integral Core's ``ServiceAuthMiddleware`` authenticates ``/api/mcp`` (and
``/api/agentive/*``) requests carrying::

    X-Integral-Service-Key   shared secret (== Core INTEGRAL_SERVICE_KEY)
    X-Integral-User-Id       the principal the key acts as
    X-Integral-Timestamp     unix seconds (within 300s of Core)
    X-Integral-Signature     HMAC-SHA256(key, "{timestamp}.{user_id}")
    X-Integral-Scope         optional "ws:<workspace_id>" scope hint

The timestamp + signature are per-request, so a static header dict cannot be
used. ``ServiceKeyAuth`` recomputes them on every request via httpx's auth flow,
and is installed on an ``httpx.AsyncClient`` that is passed to the MCP SDK's
``streamable_http_client``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 60.0


def compute_signature(service_key: str, timestamp: str, user_id: str) -> str:
    """Canonical HMAC-SHA256 over ``{timestamp}.{user_id}`` (mirrors Core)."""
    return hmac.new(
        service_key.encode("utf-8"),
        f"{timestamp}.{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class ServiceKeyAuth(httpx.Auth):
    """Signs every request with Core's service-key envelope."""

    def __init__(
        self,
        service_key: str,
        user_id: str,
        workspace_id: str = "",
    ) -> None:
        if not service_key:
            raise ValueError("service_key is required")
        if not user_id:
            raise ValueError("user_id is required")
        self._service_key = service_key
        self._user_id = user_id
        self._workspace_id = (workspace_id or "").strip()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        timestamp = str(int(time.time()))
        request.headers["X-Integral-Service-Key"] = self._service_key
        request.headers["X-Integral-User-Id"] = self._user_id
        request.headers["X-Integral-Timestamp"] = timestamp
        request.headers["X-Integral-Signature"] = compute_signature(
            self._service_key, timestamp, self._user_id
        )
        if self._workspace_id:
            request.headers["X-Integral-Scope"] = f"ws:{self._workspace_id}"
        yield request


def _resolve(value: str, env_name: str) -> str:
    """Return the literal *value*, else the env var named by *env_name*."""
    if value:
        return value.strip()
    if env_name:
        return (os.getenv(env_name) or "").strip()
    return ""


def build_service_key_client(
    *,
    service_key: str = "",
    key_env: str = "INTEGRAL_SERVICE_KEY",
    user_id: str = "",
    user_id_env: str = "INTEGRAL_SERVICE_USER_ID",
    workspace_id: str = "",
    workspace_id_env: str = "INTEGRAL_WORKSPACE_ID",
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` that signs every request for Core.

    Values may be given literally or via an env-var name (``*_env``). Raises
    ``ValueError`` when the service key or user id cannot be resolved, so a
    misconfigured server entry fails loudly at build time.
    """
    resolved_key = _resolve(service_key, key_env)
    resolved_user = _resolve(user_id, user_id_env)
    resolved_ws = _resolve(workspace_id, workspace_id_env)
    if not resolved_key:
        raise ValueError(
            "MCP service-key auth is not configured: "
            f"set '{key_env}' (or a literal service_key)"
        )
    if not resolved_user:
        raise ValueError(
            "MCP service-key auth is missing a user id: "
            f"set '{user_id_env}' (or a literal user_id)"
        )
    timeout = httpx.Timeout(
        connect=connect_timeout, read=read_timeout, write=read_timeout, pool=None
    )
    return httpx.AsyncClient(
        auth=ServiceKeyAuth(resolved_key, resolved_user, resolved_ws),
        timeout=timeout,
    )
