"""OAuth state store tests (AUDIT-actions XC-2).

Verifies one-shot consumption, expiry, provider mismatch rejection, opaque
token generation, and that the ``state`` returned to callers does NOT
contain the action_id or code_verifier.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.utils.oauth_state import (
    DEFAULT_TTL_SECONDS,
    STATE_TOKEN_BYTES,
    OAuthState,
    consume_oauth_state,
    create_oauth_state,
)


@pytest.mark.asyncio
async def test_create_returns_opaque_token_not_containing_secrets():
    created: dict = {}

    async def _fake_create(**kwargs):
        created.update(kwargs)
        return OAuthState(**kwargs)

    with patch.object(OAuthState, "create", new=AsyncMock(side_effect=_fake_create)):
        token = await create_oauth_state(
            action_id="act_123",
            provider="google",
            code_verifier="VERIFIER_SECRET_DO_NOT_LEAK",
            redirect_uri="http://example.com/cb",
        )

    # Token is base64url (urlsafe), so it must NOT contain the action_id or
    # the PKCE verifier — those live only in the DB row.
    assert "act_123" not in token
    assert "VERIFIER_SECRET_DO_NOT_LEAK" not in token
    assert len(token) >= 32
    assert created["state_token"] == token
    assert created["action_id"] == "act_123"
    assert created["provider"] == "google"
    assert created["code_verifier"] == "VERIFIER_SECRET_DO_NOT_LEAK"


@pytest.mark.asyncio
async def test_consume_one_shot_deletes_row_even_when_valid():
    fake = OAuthState(
        state_token="tok_x",
        action_id="act_1",
        provider="google",
        code_verifier="v1",
        redirect_uri="http://example.com/cb",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    delete_mock = AsyncMock()
    with patch.object(OAuthState, "find", new=AsyncMock(return_value=[fake])), patch.object(
        OAuthState, "delete", new=delete_mock
    ):
        result = await consume_oauth_state("tok_x", provider="google")

    assert result is not None
    assert result.action_id == "act_1"
    assert result.code_verifier == "v1"
    delete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_wrong_provider_rejected_and_deleted():
    """Mismatched provider must reject. Row is still deleted to prevent
    replay against the right provider later."""
    fake = OAuthState(
        state_token="tok_x",
        action_id="act_1",
        provider="microsoft",
        code_verifier="v1",
        redirect_uri="http://example.com/cb",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    delete_mock = AsyncMock()
    with patch.object(OAuthState, "find", new=AsyncMock(return_value=[fake])), patch.object(
        OAuthState, "delete", new=delete_mock
    ):
        result = await consume_oauth_state("tok_x", provider="google")

    assert result is None
    delete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_expired_rejected_and_deleted():
    fake = OAuthState(
        state_token="tok_x",
        action_id="act_1",
        provider="google",
        code_verifier="v1",
        redirect_uri="http://example.com/cb",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    delete_mock = AsyncMock()
    with patch.object(OAuthState, "find", new=AsyncMock(return_value=[fake])), patch.object(
        OAuthState, "delete", new=delete_mock
    ):
        result = await consume_oauth_state("tok_x", provider="google")

    assert result is None
    delete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_consume_unknown_state_returns_none():
    with patch.object(OAuthState, "find", new=AsyncMock(return_value=[])):
        result = await consume_oauth_state("nope", provider="google")
    assert result is None


@pytest.mark.asyncio
async def test_consume_empty_state_returns_none_without_db_call():
    find_mock = AsyncMock()
    with patch.object(OAuthState, "find", new=find_mock):
        result = await consume_oauth_state("", provider="google")
    assert result is None
    find_mock.assert_not_awaited()


def test_constants_sane():
    assert DEFAULT_TTL_SECONDS > 0
    assert STATE_TOKEN_BYTES >= 16
