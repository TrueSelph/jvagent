"""Smoke tests for Google OAuth callback route (mocked)."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_google_stubs() -> None:
    if getattr(_ensure_google_stubs, "_done", False):
        return

    google = ModuleType("google")
    google.__path__ = []  # type: ignore[attr-defined]

    auth = ModuleType("google.auth")
    transport = ModuleType("google.auth.transport")
    requests_mod = ModuleType("google.auth.transport.requests")
    requests_mod.Request = MagicMock()
    transport.requests = requests_mod
    auth.transport = transport

    oauth2 = ModuleType("google.oauth2")
    credentials = ModuleType("google.oauth2.credentials")
    credentials.Credentials = MagicMock()
    oauth2.credentials = credentials

    oauthlib = ModuleType("google_auth_oauthlib")
    flow_mod = ModuleType("google_auth_oauthlib.flow")
    flow_mod.Flow = MagicMock()
    oauthlib.flow = flow_mod

    apiclient = ModuleType("googleapiclient")
    discovery = ModuleType("googleapiclient.discovery")
    discovery.build = MagicMock()
    apiclient.discovery = discovery

    google.auth = auth
    google.oauth2 = oauth2

    for name, mod in [
        ("google", google),
        ("google.auth", auth),
        ("google.auth.transport", transport),
        ("google.auth.transport.requests", requests_mod),
        ("google.oauth2", oauth2),
        ("google.oauth2.credentials", credentials),
        ("google_auth_oauthlib", oauthlib),
        ("google_auth_oauthlib.flow", flow_mod),
        ("googleapiclient", apiclient),
        ("googleapiclient.discovery", discovery),
    ]:
        sys.modules.setdefault(name, mod)

    _ensure_google_stubs._done = True  # type: ignore[attr-defined]


_ensure_google_stubs()

from jvagent.action.google.endpoints import google_oauth_callback  # noqa: E402


@pytest.mark.asyncio
async def test_google_oauth_callback_rejects_missing_code_or_state() -> None:
    resp = await google_oauth_callback(code="", state="tok")
    assert resp.status_code == 400
    assert b"Missing code or state" in resp.body

    resp2 = await google_oauth_callback(code="auth-code", state="")
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_google_oauth_callback_rejects_invalid_state() -> None:
    with patch(
        "jvagent.action.oauth.state.consume_oauth_state",
        new=AsyncMock(return_value=None),
    ):
        resp = await google_oauth_callback(code="auth-code", state="bad-state")

    assert resp.status_code == 400
    assert b"invalid, expired, or already used" in resp.body
