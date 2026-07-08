"""Smoke tests for Microsoft OAuth callback route (mocked)."""

from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.microsoft.endpoints import microsoft_oauth_callback


@pytest.mark.asyncio
async def test_microsoft_oauth_callback_rejects_missing_code_or_state() -> None:
    resp = await microsoft_oauth_callback(code="", state="tok")
    assert resp.status_code == 400
    assert b"Missing code or state" in resp.body


@pytest.mark.asyncio
async def test_microsoft_oauth_callback_rejects_invalid_state() -> None:
    with patch(
        "jvagent.action.oauth.state.consume_oauth_state",
        new=AsyncMock(return_value=None),
    ):
        resp = await microsoft_oauth_callback(code="auth-code", state="bad-state")

    assert resp.status_code == 400
    assert b"invalid, expired, or already used" in resp.body
