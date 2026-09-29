"""Google OAuth callback must persist an access-token expiry.

Regression: ``_exchange_google_code`` wrote a payload without ``expiry``.
google-auth's ``from_authorized_user_info`` then defaults expiry to
``utcnow() - REFRESH_THRESHOLD``, so every freshly authorized token was treated
as already expired and an immediate refresh fired on first use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.mcp_oauth import endpoints as ep


def _fake_httpx_client(token_response: dict, info_response: dict):
    """An ``httpx.AsyncClient`` stand-in for the token + userinfo calls."""

    class _Resp:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self._token_response = token_response
            self._info_response = info_response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            return _Resp(self._token_response)

        async def get(self, url, headers=None, timeout=None):
            return _Resp(self._info_response)

    return _Client


@pytest.mark.asyncio
async def test_google_callback_persists_future_expiry(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_CLIENT_SECRETS_JSON",
        '{"web": {"client_id": "cid", "client_secret": "cs", '
        '"auth_uri": "https://accounts.google.com/o/oauth2/auth", '
        '"token_uri": "https://oauth2.googleapis.com/token"}}',
    )

    saved: dict = {}

    action = SimpleNamespace(
        id="n.MCPOAuthAction.test",
        agent_id="a1",
        enabled=True,
    )

    async def _save(server, account, payload, service=None):
        saved["server"] = server
        saved["account"] = account
        saved["payload"] = payload

    action.save_oauth_token_for_service = AsyncMock(side_effect=_save)

    record = SimpleNamespace(redirect_uri="https://x/callback")

    tokens = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3599,
        "scope": "https://www.googleapis.com/auth/spreadsheets",
    }
    info = {"email": "ops@example.com"}

    with (
        patch.object(
            ep,
            "_google_workspace_mcp_tool_config",
            new=AsyncMock(return_value=({}, {})),
        ),
        patch.object(
            ep,
            "_enabled_google_oauth_services",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", _fake_httpx_client(tokens, info)),
    ):
        result = await ep._exchange_google_code(
            action, "code", record, "integral", service="sheets"
        )

    assert not isinstance(result, ep.HTMLResponse)

    payload = saved["payload"]
    assert payload.get("expiry"), "Google token payload must carry an expiry"

    expiry = datetime.fromisoformat(payload["expiry"])
    now = datetime.now(timezone.utc)
    # Roughly expires_in seconds out, minus the 60s safety margin.
    assert now < expiry <= now + timedelta(seconds=3600)
    assert payload["expiry"].endswith("+00:00")


@pytest.mark.asyncio
async def test_google_callback_defaults_expiry_when_absent(monkeypatch):
    """A token response without ``expires_in`` still gets a future expiry."""
    monkeypatch.setenv(
        "GOOGLE_CLIENT_SECRETS_JSON",
        '{"web": {"client_id": "cid", "client_secret": "cs"}}',
    )

    saved: dict = {}
    action = SimpleNamespace(id="n.MCPOAuthAction.test", agent_id="a1", enabled=True)

    async def _save(server, account, payload, service=None):
        saved["payload"] = payload

    action.save_oauth_token_for_service = AsyncMock(side_effect=_save)
    record = SimpleNamespace(redirect_uri="https://x/callback")

    tokens = {"access_token": "at", "refresh_token": "rt"}
    with (
        patch.object(
            ep,
            "_google_workspace_mcp_tool_config",
            new=AsyncMock(return_value=({}, {})),
        ),
        patch.object(
            ep,
            "_enabled_google_oauth_services",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", _fake_httpx_client(tokens, {"email": "o@e.com"})),
    ):
        await ep._exchange_google_code(action, "code", record, "integral", service="")

    expiry = datetime.fromisoformat(saved["payload"]["expiry"])
    assert expiry > datetime.now(timezone.utc)
