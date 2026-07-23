"""Session-token refresh for the public interact endpoint (ADR-0032):
grace-window verification, token exchange bound to the conversation, and the
refresh endpoint's declared response schema."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Optional

import jwt
import pytest

from jvagent.action.interact import session_token as st

_SECRET = "unit-test-secret-key-aaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", _SECRET)
    monkeypatch.setenv("JVAGENT_INTERACT_PUBLIC_AUTH", "required")
    yield


def _conv(**kw):
    base = dict(channel="default", session_id="s1", user_id="u1", token_secret="cs1")
    base.update(kw)
    return SimpleNamespace(**base)


def _agent_with(conversation: Any):
    async def get_conversation_by_session(session_id):
        if conversation is not None and conversation.session_id == session_id:
            return conversation
        return None

    memory = SimpleNamespace(get_conversation_by_session=get_conversation_by_session)

    async def get_memory():
        return memory

    return SimpleNamespace(get_memory=get_memory)


def _request(token: Optional[str] = None):
    headers = {}
    if token is not None:
        headers["x-session-token"] = token
    return SimpleNamespace(headers=headers)


def _mint(ttl: int = 3600, **kw) -> str:
    base = dict(agent_id="a1", session_id="s1", user_id="u1", token_secret="cs1")
    base.update(kw)
    return st.mint_session_token(ttl_seconds=ttl, **base)


def _expired(seconds_past_exp: int) -> str:
    tok = _mint()
    payload = jwt.decode(tok, _SECRET, algorithms=["HS256"])
    payload["exp"] = int(time.time()) - seconds_past_exp
    return jwt.encode(payload, _SECRET, algorithm="HS256")


# --- refresh_grace_seconds ----------------------------------------------------


def test_refresh_grace_default_and_overrides(monkeypatch):
    assert st.refresh_grace_seconds() == 60 * 60 * 24 * 7
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "0")
    assert st.refresh_grace_seconds() == 0
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "120")
    assert st.refresh_grace_seconds() == 120
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "-5")
    assert st.refresh_grace_seconds() == 60 * 60 * 24 * 7
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "bogus")
    assert st.refresh_grace_seconds() == 60 * 60 * 24 * 7


# --- verify_session_token_for_refresh ----------------------------------------


def test_refresh_verify_accepts_valid_token():
    claims, err = st.verify_session_token_for_refresh(_mint(), expected_agent_id="a1")
    assert err is None and claims["session_id"] == "s1"


def test_refresh_verify_accepts_expired_within_grace():
    claims, err = st.verify_session_token_for_refresh(_expired(60))
    assert err is None and claims is not None


def test_refresh_verify_rejects_expired_beyond_grace(monkeypatch):
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "30")
    claims, err = st.verify_session_token_for_refresh(_expired(60))
    assert claims is None and err == "expired_beyond_grace"


def test_refresh_verify_zero_grace_rejects_any_expired(monkeypatch):
    monkeypatch.setenv("JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS", "0")
    claims, err = st.verify_session_token_for_refresh(_expired(1))
    assert claims is None and err == "expired_beyond_grace"


def test_refresh_verify_rejects_tampered_signature():
    forged = jwt.encode(
        jwt.decode(_mint(), _SECRET, algorithms=["HS256"]),
        "other-secret",
        algorithm="HS256",
    )
    claims, err = st.verify_session_token_for_refresh(forged)
    assert claims is None and err and err.startswith("invalid")


def test_refresh_verify_rejects_agent_mismatch():
    claims, err = st.verify_session_token_for_refresh(_mint(), expected_agent_id="a2")
    assert claims is None and err == "agent_mismatch"


def test_refresh_verify_rejects_bearer_typed_token():
    login = jwt.encode(
        {"user_id": "alice", "exp": int(time.time()) + 60}, _SECRET, algorithm="HS256"
    )
    claims, err = st.verify_session_token_for_refresh(login)
    assert claims is None and err == "wrong_type"


def test_refresh_verify_rejects_missing_exp():
    payload = jwt.decode(_mint(), _SECRET, algorithms=["HS256"])
    del payload["exp"]
    tok = jwt.encode(payload, _SECRET, algorithm="HS256")
    claims, err = st.verify_session_token_for_refresh(tok)
    assert claims is None and err == "invalid:MissingExpiry"


def test_refresh_verify_no_secret(monkeypatch):
    tok = _mint()
    monkeypatch.delenv("JVSPATIAL_JWT_SECRET_KEY", raising=False)
    claims, err = st.verify_session_token_for_refresh(tok)
    assert claims is None and err == "no_secret_configured"


# --- refresh_session_token (the exchange) -------------------------------------


@pytest.mark.asyncio
async def test_refresh_exchange_returns_fresh_token():
    old = _mint(ttl=60)
    new_token, claims, err = await st.refresh_session_token(
        request=_request(old), agent=_agent_with(_conv()), agent_id="a1"
    )
    assert err is None and new_token and claims is not None
    new_claims, verr = st.verify_session_token(new_token, expected_agent_id="a1")
    assert verr is None
    assert new_claims["session_id"] == "s1" and new_claims["user_id"] == "u1"
    assert new_claims["cs"] == "cs1"
    old_claims = jwt.decode(old, _SECRET, algorithms=["HS256"])
    assert new_claims["exp"] > old_claims["exp"]
    assert new_claims["jti"] != old_claims["jti"]


@pytest.mark.asyncio
async def test_refresh_exchange_accepts_body_token_over_header():
    tok = _mint()
    new_token, _, err = await st.refresh_session_token(
        request=_request(None),
        agent=_agent_with(_conv()),
        agent_id="a1",
        session_token=tok,
    )
    assert err is None and new_token


@pytest.mark.asyncio
async def test_refresh_exchange_recovers_expired_within_grace():
    new_token, _, err = await st.refresh_session_token(
        request=_request(_expired(60)), agent=_agent_with(_conv()), agent_id="a1"
    )
    assert err is None and new_token


@pytest.mark.asyncio
async def test_refresh_exchange_missing_token():
    new_token, claims, err = await st.refresh_session_token(
        request=_request(None), agent=_agent_with(_conv()), agent_id="a1"
    )
    assert new_token is None and claims is None and err == "missing_token"


@pytest.mark.asyncio
async def test_refresh_exchange_no_conversation():
    new_token, _, err = await st.refresh_session_token(
        request=_request(_mint()), agent=_agent_with(None), agent_id="a1"
    )
    assert new_token is None and err == "no_conversation"


@pytest.mark.asyncio
async def test_refresh_exchange_rotated_secret_revokes():
    new_token, _, err = await st.refresh_session_token(
        request=_request(_mint()),
        agent=_agent_with(_conv(token_secret="rotated")),
        agent_id="a1",
    )
    assert new_token is None and err == "bind_secret_mismatch"


@pytest.mark.asyncio
async def test_refresh_exchange_cross_channel_rejected():
    new_token, _, err = await st.refresh_session_token(
        request=_request(_mint()),
        agent=_agent_with(_conv(channel="whatsapp")),
        agent_id="a1",
    )
    assert new_token is None and err == "bind_cross_channel"


# --- endpoint schema -----------------------------------------------------------


def test_refresh_endpoint_declares_response_fields():
    """The refresh endpoint must declare its fields or the generated response
    model (extra="ignore") silently drops them from the JSON body."""
    from jvagent.action.interact.endpoints import interact_session_refresh_endpoint

    cfg = getattr(interact_session_refresh_endpoint, "_jvspatial_endpoint_config", None)
    assert cfg is not None
    schema = cfg.get("response")
    assert schema is not None
    declared = schema.data or {}
    for field in ("session_id", "user_id", "session_token", "expires_in"):
        assert field in declared

    model = schema.to_pydantic_model("InteractSessionRefreshResponseTest")
    dumped = model(
        success=True,
        session_id="s1",
        user_id="u1",
        session_token="tok123",
        expires_in=604800,
    ).model_dump(exclude_none=True)
    assert dumped.get("session_token") == "tok123"
    assert dumped.get("expires_in") == 604800
