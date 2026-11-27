"""MCP transport httpx clients must follow Core's ``/api/mcp`` trailing-slash 307.

Core advertises the MCP endpoint without a trailing slash; its Starlette Mount
answers ``POST /api/mcp`` with a 307 to ``/api/mcp/``. The MCP SDK consumes the
client we pass as-is and raises on that response, so both auth clients must
follow redirects — but only to the configured trusted origin.
"""

from __future__ import annotations

import httpx
import pytest

from jvagent.action.mcp.integral_oauth_auth import build_integral_oauth_client
from jvagent.action.mcp.redirect_trust import UntrustedRedirectError
from jvagent.action.mcp.service_key_auth import build_service_key_client

_TRUSTED = "https://core.invalid/api/mcp"
_SERVICE_KEY = "k" * 32


async def test_integral_oauth_client_follows_redirects():
    client = build_integral_oauth_client(
        server_name="integral_core",
        account="integral",
        mcp_url=_TRUSTED,
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        assert client.follow_redirects is True
    finally:
        await client.aclose()


async def test_service_key_client_follows_redirects():
    client = build_service_key_client(
        service_key=_SERVICE_KEY,
        user_id="u",
        mcp_url=_TRUSTED,
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        assert client.follow_redirects is True
    finally:
        await client.aclose()


async def test_integral_client_stores_configured_scope():
    client = build_integral_oauth_client(
        server_name="integral_core",
        account="integral",
        scope="integral:read integral:execute",
        mcp_url="http://127.0.0.1:54853/api/mcp/",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        assert client._auth._scope == "integral:read integral:execute"
        assert client._auth._mcp_url == "http://127.0.0.1:54853/api/mcp/"
    finally:
        await client.aclose()


class _FakeNode:
    pass


async def test_integral_refresh_prefers_configured_scope(monkeypatch):
    import jvagent.action.mcp.integral_oauth_auth as mod

    captured = {}

    async def _fake_discover(**kwargs):
        return {"token_endpoint": "http://core.invalid/api/oauth/token"}

    async def _fake_refresh(meta, *, client_id, refresh_token, scope):
        captured["scope"] = scope
        return {"access_token": "at", "expires_in": 3600, "refresh_token": "rt"}

    async def _fake_save(node, payload):
        captured["payload"] = payload

    def _env_scope(*a, **k):
        return "env:scope"

    monkeypatch.setattr(mod, "discover_authorization_server", _fake_discover)
    monkeypatch.setattr(mod, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(mod, "_save_token_payload", _fake_save)
    monkeypatch.setattr(mod, "resolve_scope", _env_scope)

    auth = mod.IntegralOAuthAuth(
        server_name="integral_core",
        account_name="integral",
        scope="integral:read integral:execute",
    )
    payload = {
        "refresh_token": "rt",
        "client_id": "cid",
        "access_token": "old",
        "token_uri": "http://core.invalid/api/oauth/token",
        "expiry": "2000-01-01T00:00:00Z",
    }
    token = await auth._refresh(_FakeNode(), payload)
    assert token == "at"
    assert captured["scope"] == "integral:read integral:execute"


async def test_integral_refresh_falls_back_to_env_scope(monkeypatch):
    import jvagent.action.mcp.integral_oauth_auth as mod

    captured = {}

    async def _fake_discover(**kwargs):
        return {"token_endpoint": "http://core.invalid/api/oauth/token"}

    async def _fake_refresh(meta, *, client_id, refresh_token, scope):
        captured["scope"] = scope
        return {"access_token": "at", "expires_in": 3600}

    async def _fake_save(node, payload):
        pass

    def _env_scope(*a, **k):
        return "integral:read"

    monkeypatch.setattr(mod, "discover_authorization_server", _fake_discover)
    monkeypatch.setattr(mod, "refresh_access_token", _fake_refresh)
    monkeypatch.setattr(mod, "_save_token_payload", _fake_save)
    monkeypatch.setattr(mod, "resolve_scope", _env_scope)

    auth = mod.IntegralOAuthAuth(server_name="integral_core", account_name="integral")
    payload = {
        "refresh_token": "rt",
        "client_id": "cid",
        "access_token": "old",
        "expiry": "2000-01-01T00:00:00Z",
    }
    await auth._refresh(_FakeNode(), payload)
    assert captured["scope"] == "integral:read"


def _oauth_client_with_token(transport: httpx.MockTransport, mcp_url: str):
    client = build_integral_oauth_client(
        server_name="integral_core",
        account="integral",
        mcp_url=mcp_url,
        connect_timeout=1.0,
        read_timeout=1.0,
        transport=transport,
    )

    async def _fake_token():
        return "tok"

    client._auth._valid_token = _fake_token
    return client


def _service_key_client(transport: httpx.MockTransport, mcp_url: str):
    return build_service_key_client(
        service_key=_SERVICE_KEY,
        user_id="u",
        mcp_url=mcp_url,
        connect_timeout=1.0,
        read_timeout=1.0,
        transport=transport,
    )


async def test_oauth_same_origin_trailing_slash_keeps_bearer():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("authorization"),
            )
        )
        if request.url.path.rstrip("/") == "/api/mcp" and not request.url.path.endswith(
            "/"
        ):
            return httpx.Response(
                307, headers={"location": "/api/mcp/"}, request=request
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    client = _oauth_client_with_token(httpx.MockTransport(handler), _TRUSTED)
    try:
        resp = await client.post(_TRUSTED)
        assert resp.status_code == 200
    finally:
        await client.aclose()

    assert len(seen) == 2
    assert seen[0][0] == "https://core.invalid/api/mcp"
    assert seen[1][0] == "https://core.invalid/api/mcp/"
    assert seen[0][1] == "Bearer tok"
    assert seen[1][1] == "Bearer tok"


async def test_oauth_cross_origin_redirect_never_sends_bearer():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("authorization"),
            )
        )
        if "core.invalid" in request.url.host:
            return httpx.Response(
                307,
                headers={"location": "https://other.invalid/api/mcp/"},
                request=request,
            )
        return httpx.Response(200, json={"leaked": True}, request=request)

    client = _oauth_client_with_token(httpx.MockTransport(handler), _TRUSTED)
    try:
        with pytest.raises(UntrustedRedirectError):
            await client.post(_TRUSTED)
    finally:
        await client.aclose()

    assert len(seen) == 1
    assert seen[0][0] == "https://core.invalid/api/mcp"
    assert all("other.invalid" not in url for url, _ in seen)


async def test_oauth_https_to_http_downgrade_never_sends_bearer():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("authorization"),
                request.url.scheme,
            )
        )
        if request.url.scheme == "https":
            return httpx.Response(
                307,
                headers={"location": "http://core.invalid/api/mcp/"},
                request=request,
            )
        return httpx.Response(200, json={"leaked": True}, request=request)

    client = _oauth_client_with_token(httpx.MockTransport(handler), _TRUSTED)
    try:
        with pytest.raises(UntrustedRedirectError):
            await client.post(_TRUSTED)
    finally:
        await client.aclose()

    assert len(seen) == 1
    assert seen[0][2] == "https"


async def test_service_key_same_origin_trailing_slash_keeps_headers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("x-integral-service-key"),
            )
        )
        if request.url.path.rstrip("/") == "/api/mcp" and not request.url.path.endswith(
            "/"
        ):
            return httpx.Response(
                307, headers={"location": "/api/mcp/"}, request=request
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    client = _service_key_client(httpx.MockTransport(handler), _TRUSTED)
    try:
        resp = await client.post(_TRUSTED)
        assert resp.status_code == 200
    finally:
        await client.aclose()

    assert len(seen) == 2
    assert seen[0][1] == _SERVICE_KEY
    assert seen[1][1] == _SERVICE_KEY


async def test_service_key_cross_origin_redirect_never_sends_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("x-integral-service-key"),
            )
        )
        if "core.invalid" in request.url.host:
            return httpx.Response(
                307,
                headers={"location": "https://other.invalid/api/mcp/"},
                request=request,
            )
        return httpx.Response(200, json={"leaked": True}, request=request)

    client = _service_key_client(httpx.MockTransport(handler), _TRUSTED)
    try:
        with pytest.raises(UntrustedRedirectError):
            await client.post(_TRUSTED)
    finally:
        await client.aclose()

    assert len(seen) == 1
    assert all("other.invalid" not in url for url, _ in seen)


async def test_service_key_https_to_http_downgrade_never_sends_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("x-integral-service-key"),
                request.url.scheme,
            )
        )
        if request.url.scheme == "https":
            return httpx.Response(
                307,
                headers={"location": "http://core.invalid/api/mcp/"},
                request=request,
            )
        return httpx.Response(200, json={"leaked": True}, request=request)

    client = _service_key_client(httpx.MockTransport(handler), _TRUSTED)
    try:
        with pytest.raises(UntrustedRedirectError):
            await client.post(_TRUSTED)
    finally:
        await client.aclose()

    assert len(seen) == 1
    assert seen[0][2] == "https"
