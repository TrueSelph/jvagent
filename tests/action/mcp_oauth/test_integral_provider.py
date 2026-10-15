"""Integral Core OAuth 2.1 provider: discovery, DCR, PKCE, exchange, refresh."""

import json

import pytest

from jvagent.action.mcp_oauth import integral_provider as ip
from jvagent.action.mcp_oauth.mcp_oauth_action import build_oauth_setup

BASE = "https://agent.example.com"
MCP_URL = "http://127.0.0.1:9006/api/mcp/"
ORIGIN = "http://127.0.0.1:9006"
TOKEN_URL = f"{ORIGIN}/api/oauth/token"
REGISTER_URL = f"{ORIGIN}/api/oauth/register"
AUTHORIZE_URL = f"{ORIGIN}/api/oauth/authorize"

AS_META = {
    "issuer": ORIGIN,
    "authorization_endpoint": AUTHORIZE_URL,
    "token_endpoint": TOKEN_URL,
    "registration_endpoint": REGISTER_URL,
    "code_challenge_methods_supported": ["S256"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
}
PRM = {"resource": MCP_URL, "authorization_servers": [ORIGIN]}


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    @property
    def text(self) -> str:
        return json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def _install_fake(monkeypatch, *, get=None, post=None):
    """Replace ``httpx.AsyncClient`` with a canned-response stand-in."""
    calls = {"get": [], "post": []}

    class _Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls["get"].append(str(url))
            return (get or {}).get(str(url), _Resp(404, {}))

        async def post(self, url, **kw):
            calls["post"].append((str(url), kw))
            return (post or {}).get(str(url), _Resp(404, {}))

    monkeypatch.setattr(ip.httpx, "AsyncClient", _Fake)
    return calls


# ---------------------------------------------------------------------------
# PKCE (RFC 7636 appendix B test vector)
# ---------------------------------------------------------------------------


def test_pkce_challenge_matches_rfc7636_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert ip.pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_verifier_length_is_within_rfc_bounds():
    verifier = ip.pkce_verifier()
    assert 43 <= len(verifier) <= 128


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def test_discovery_prefers_protected_resource(monkeypatch):
    _install_fake(
        monkeypatch,
        get={
            f"{ORIGIN}/.well-known/oauth-protected-resource": _Resp(200, PRM),
            f"{ORIGIN}/.well-known/oauth-authorization-server": _Resp(200, AS_META),
        },
    )
    meta = await ip.discover_authorization_server(mcp_url=MCP_URL)
    assert meta["token_endpoint"] == TOKEN_URL
    assert meta["registration_endpoint"] == REGISTER_URL


async def test_discovery_falls_back_when_docs_unreachable(monkeypatch):
    _install_fake(monkeypatch, get={})
    meta = await ip.discover_authorization_server(mcp_url=MCP_URL)
    assert meta["token_endpoint"] == TOKEN_URL
    assert meta["authorization_endpoint"] == AUTHORIZE_URL


async def test_discovery_uses_explicit_issuer(monkeypatch):
    _install_fake(
        monkeypatch,
        get={f"{ORIGIN}/.well-known/oauth-authorization-server": _Resp(200, AS_META)},
    )
    meta = await ip.discover_authorization_server(issuer=ORIGIN)
    assert meta["token_endpoint"] == TOKEN_URL


# ---------------------------------------------------------------------------
# Dynamic Client Registration
# ---------------------------------------------------------------------------


async def test_register_client_posts_public_client(monkeypatch):
    calls = _install_fake(
        monkeypatch,
        post={REGISTER_URL: _Resp(201, {"client_id": "dcr_client_123"})},
    )
    client_id = await ip.register_client(
        AS_META,
        redirect_uri=f"{BASE}/api/mcp/integral_core/auth/callback",
        scope="",
    )
    assert client_id == "dcr_client_123"
    url, kw = calls["post"][0]
    assert url == REGISTER_URL
    body = kw["json"]
    assert body["token_endpoint_auth_method"] == "none"
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["redirect_uris"] == [f"{BASE}/api/mcp/integral_core/auth/callback"]


async def test_register_client_raises_on_error(monkeypatch):
    _install_fake(monkeypatch, post={REGISTER_URL: _Resp(400, {"error": "x"})})
    with pytest.raises(ip.IntegralOAuthServiceError):
        await ip.register_client(AS_META, redirect_uri="https://x/cb")


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


def test_build_authorize_url_carries_pkce_s256():
    url = ip.build_authorize_url(
        AS_META,
        client_id="cid",
        redirect_uri="https://x/cb",
        state="st",
        code_challenge="ch",
        scope="",
    )
    assert url.startswith(AUTHORIZE_URL + "?")
    assert "code_challenge_method=S256" in url
    assert "code_challenge=ch" in url
    assert "client_id=cid" in url


# ---------------------------------------------------------------------------
# Token exchange + refresh
# ---------------------------------------------------------------------------


async def test_exchange_authorization_code(monkeypatch):
    calls = _install_fake(
        monkeypatch,
        post={
            TOKEN_URL: _Resp(
                200,
                {
                    "access_token": "at_1",
                    "refresh_token": "rt_1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        },
    )
    tokens = await ip.exchange_authorization_code(
        AS_META,
        client_id="cid",
        code="code",
        code_verifier="verifier",
        redirect_uri="https://x/cb",
    )
    assert tokens["access_token"] == "at_1"
    _url, kw = calls["post"][0]
    assert kw["data"]["grant_type"] == "authorization_code"
    assert kw["data"]["code_verifier"] == "verifier"
    assert kw["data"]["client_id"] == "cid"


async def test_refresh_access_token(monkeypatch):
    calls = _install_fake(
        monkeypatch,
        post={TOKEN_URL: _Resp(200, {"access_token": "at_2", "expires_in": 3600})},
    )
    tokens = await ip.refresh_access_token(
        AS_META, client_id="cid", refresh_token="rt_1"
    )
    assert tokens["access_token"] == "at_2"
    _url, kw = calls["post"][0]
    assert kw["data"]["grant_type"] == "refresh_token"
    assert kw["data"]["refresh_token"] == "rt_1"


# ---------------------------------------------------------------------------
# Stored payload helpers
# ---------------------------------------------------------------------------


def test_normalize_token_payload_keeps_previous_refresh():
    payload = ip.normalize_token_payload(
        {"access_token": "at", "expires_in": 3600},
        client_id="cid",
        token_uri=TOKEN_URL,
        account_name="integral",
        previous={"refresh_token": "rt_old"},
    )
    assert payload["refresh_token"] == "rt_old"
    assert payload["access_token"] == "at"
    assert payload["type"] == "oauth2"


def test_payload_is_expired():
    assert ip.payload_is_expired(None) is True
    assert ip.payload_is_expired({"access_token": "x"}) is False
    assert (
        ip.payload_is_expired({"access_token": "x", "expiry": "2000-01-01T00:00:00Z"})
        is True
    )


# ---------------------------------------------------------------------------
# URL helpers + oauth_setup exposure
# ---------------------------------------------------------------------------


def test_mcp_integral_core_auth_url():
    url = ip.mcp_integral_core_auth_url(account="integral", base_url=BASE)
    assert url == f"{BASE}/api/mcp/integral_core/auth?account=integral"


def test_build_oauth_setup_includes_integral():
    setup = build_oauth_setup(BASE, [{"name": "integral_core"}])
    assert len(setup) == 1
    entry = setup[0]
    assert entry["server"] == "integral_core"
    assert entry["service"] == "integral"
    assert entry["label"] == "Integral"
    assert entry["redirect_uri"] == f"{BASE}/api/mcp/integral_core/auth/callback"
    assert entry["auth_url"] == (
        f"{BASE}/api/mcp/integral_core/auth?account=integral&service=integral"
    )


def test_build_oauth_setup_absent_when_not_configured():
    setup = build_oauth_setup(BASE, [{"name": "google_workspace"}])
    assert all(item["server"] != "integral_core" for item in setup)


# ---------------------------------------------------------------------------
# Scope passthrough (verbatim — authored on the server entry)
# ---------------------------------------------------------------------------


def test_join_scopes_verbatim_from_list():
    assert ip.join_scopes(["integral:read", "integral:execute"]) == (
        "integral:read integral:execute"
    )


def test_join_scopes_passes_through_unknown_tokens():
    # No aliasing/filtering: whatever is authored is forwarded (Core validates).
    assert ip.join_scopes(["entry.create", "integral:admin"]) == (
        "entry.create integral:admin"
    )
    assert ip.join_scopes(["integral:write"]) == "integral:write"


def test_join_scopes_accepts_string_and_splits_commas_or_spaces():
    assert ip.join_scopes("integral:read, integral:execute") == (
        "integral:read integral:execute"
    )
    assert ip.join_scopes("  integral:propose   integral:read ") == (
        "integral:propose integral:read"
    )


def test_join_scopes_empty():
    assert ip.join_scopes(None) == ""
    assert ip.join_scopes([]) == ""
    assert ip.join_scopes([" ", ""]) == ""


def test_resolve_scopes_from_auth_precedence(monkeypatch):
    monkeypatch.setenv("INTEGRAL_OAUTH_SCOPE", "integral:propose")
    # auth.scopes (list) wins, verbatim
    assert (
        ip.resolve_scopes_from_auth({"scopes": ["integral:read", "integral:execute"]})
        == "integral:read integral:execute"
    )
    # auth.scope (string) verbatim
    assert ip.resolve_scopes_from_auth({"scope": "integral:read"}) == "integral:read"
    # env fallback when the auth block has neither
    assert ip.resolve_scopes_from_auth({}) == "integral:propose"
    # scopes wins over scope
    assert (
        ip.resolve_scopes_from_auth(
            {"scopes": ["entry.create"], "scope": "integral:propose"}
        )
        == "entry.create"
    )
