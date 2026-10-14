"""MCP transport httpx clients must follow Core's ``/api/mcp`` trailing-slash 307.

Core advertises the MCP endpoint without a trailing slash; its Starlette Mount
answers ``POST /api/mcp`` with a 307 to ``/api/mcp/``. The MCP SDK consumes the
client we pass as-is and raises on that response, so both auth clients must
follow redirects.
"""

from jvagent.action.mcp.integral_oauth_auth import build_integral_oauth_client
from jvagent.action.mcp.service_key_auth import build_service_key_client


async def test_integral_oauth_client_follows_redirects():
    client = build_integral_oauth_client(
        server_name="integral_core",
        account="integral",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        assert client.follow_redirects is True
    finally:
        await client.aclose()


async def test_service_key_client_follows_redirects():
    client = build_service_key_client(
        service_key="k" * 32,
        user_id="u",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        assert client.follow_redirects is True
    finally:
        await client.aclose()


def _request_auth_hook(client, name):
    hooks = [h for h in client._event_hooks["request"] if h.__name__ == name]
    assert hooks, f"missing request hook {name}"
    return hooks[0]


async def test_integral_hook_reapplies_bearer_on_redirect_hop():
    """A cross-origin redirect strips ``Authorization``; the hook must re-add it."""

    async def _fake_token():
        return "tok"

    client = build_integral_oauth_client(
        server_name="integral_core",
        account="integral",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        client._auth._valid_token = _fake_token
        request = client.build_request("POST", "https://example.invalid/api/mcp/")
        if "authorization" in request.headers:
            del request.headers["authorization"]
        await _request_auth_hook(client, "_ensure_auth")(request)
        assert request.headers["Authorization"] == "Bearer tok"
    finally:
        await client.aclose()


async def test_service_key_hook_resigns_on_redirect_hop():
    client = build_service_key_client(
        service_key="k" * 32,
        user_id="u",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        request = client.build_request("POST", "https://example.invalid/api/mcp/")
        if "x-integral-service-key" in request.headers:
            del request.headers["x-integral-service-key"]
        await _request_auth_hook(client, "_ensure_service_key")(request)
        assert request.headers["X-Integral-Service-Key"] == "k" * 32
        assert request.headers["X-Integral-Signature"]
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
