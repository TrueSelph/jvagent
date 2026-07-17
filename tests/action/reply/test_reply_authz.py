"""Authorization for the reply publish/subscribe surface (AUDIT-actions HIGH).

Without a session-ownership check these endpoints are an IDOR: any authenticated
caller could read/drain any session's messages (subscribe) or inject
agent-attributed content into any session (publish). These tests drive the
endpoint functions directly with mocked identity + graph."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.reply import endpoints as reply_endpoints

pytestmark = pytest.mark.asyncio


def _request(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


def _install_agent(monkeypatch, *, conversation):
    """Patch Agent.get + response bus so the endpoints resolve a fake agent."""
    bus = MagicMock()
    bus._session_queues = {"s1": [MagicMock(to_dict=lambda: {"m": 1})]}
    bus.publish = AsyncMock(return_value=SimpleNamespace(id="msg1"))

    memory = SimpleNamespace(
        get_conversation_by_session=AsyncMock(return_value=conversation)
    )
    agent = SimpleNamespace(
        id="a1",
        get_response_bus=AsyncMock(return_value=bus),
        get_memory=AsyncMock(return_value=memory),
    )
    monkeypatch.setattr(reply_endpoints.Agent, "get", AsyncMock(return_value=agent))
    return agent, bus


def _patch_tokens(monkeypatch, *, bearer_uid=None, session_claims=None):
    from jvagent.action.interact import session_token as st

    monkeypatch.setattr(st, "verify_bearer", lambda tok: bearer_uid)
    monkeypatch.setattr(
        st,
        "verify_session_token",
        lambda tok, expected_agent_id=None: (session_claims, None),
    )


# --- Mode A bearer: conversation ownership ------------------------------------


async def test_subscribe_bearer_owner_allowed(monkeypatch):
    _patch_tokens(monkeypatch, bearer_uid="alice")
    conv = SimpleNamespace(user_id="alice")
    _install_agent(monkeypatch, conversation=conv)

    result = await reply_endpoints.reply_subscribe_endpoint(
        _request({"authorization": "Bearer tok"}), "a1", "s1", stream=False
    )
    assert result["ok"] is True


async def test_subscribe_bearer_non_owner_forbidden(monkeypatch):
    from jvspatial.api.exceptions import AuthorizationError

    _patch_tokens(monkeypatch, bearer_uid="mallory")
    conv = SimpleNamespace(user_id="alice")  # owned by someone else
    _, bus = _install_agent(monkeypatch, conversation=conv)

    with pytest.raises(AuthorizationError):
        await reply_endpoints.reply_subscribe_endpoint(
            _request({"authorization": "Bearer tok"}), "a1", "s1", stream=False
        )
    # The victim's queue must NOT have been drained.
    assert "s1" in bus._session_queues


async def test_subscribe_bearer_missing_conversation_forbidden(monkeypatch):
    from jvspatial.api.exceptions import AuthorizationError

    _patch_tokens(monkeypatch, bearer_uid="alice")
    _install_agent(monkeypatch, conversation=None)

    with pytest.raises(AuthorizationError):
        await reply_endpoints.reply_subscribe_endpoint(
            _request({"authorization": "Bearer tok"}), "a1", "s1", stream=False
        )


# --- Mode B session token: bound session --------------------------------------


async def test_subscribe_session_token_bound_session_allowed(monkeypatch):
    _patch_tokens(monkeypatch, session_claims={"user_id": "alice", "session_id": "s1"})
    _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="alice"))

    result = await reply_endpoints.reply_subscribe_endpoint(
        _request({"x-session-token": "tok"}), "a1", "s1", stream=False
    )
    assert result["ok"] is True


async def test_subscribe_session_token_other_session_forbidden(monkeypatch):
    from jvspatial.api.exceptions import AuthorizationError

    # Token bound to s2 but caller requests s1.
    _patch_tokens(monkeypatch, session_claims={"user_id": "alice", "session_id": "s2"})
    _, bus = _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="x"))

    with pytest.raises(AuthorizationError):
        await reply_endpoints.reply_subscribe_endpoint(
            _request({"x-session-token": "tok"}), "a1", "s1", stream=False
        )
    assert "s1" in bus._session_queues


# --- Publish ------------------------------------------------------------------


async def test_publish_non_owner_forbidden(monkeypatch):
    from jvspatial.api.exceptions import AuthorizationError

    _patch_tokens(monkeypatch, bearer_uid="mallory")
    _, bus = _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="alice"))

    with pytest.raises(AuthorizationError):
        await reply_endpoints.reply_publish_endpoint(
            _request({"authorization": "Bearer tok"}),
            "a1",
            message="spoofed",
            session_id="s1",
        )
    bus.publish.assert_not_called()


async def test_publish_owner_allowed(monkeypatch):
    _patch_tokens(monkeypatch, bearer_uid="alice")
    _, bus = _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="alice"))

    result = await reply_endpoints.reply_publish_endpoint(
        _request({"authorization": "Bearer tok"}),
        "a1",
        message="hi",
        session_id="s1",
    )
    assert result["ok"] is True
    bus.publish.assert_awaited()


async def test_publish_requires_session_id(monkeypatch):
    from jvspatial.api.exceptions import InvalidInputError

    _patch_tokens(monkeypatch, bearer_uid="alice")
    _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="alice"))

    with pytest.raises(InvalidInputError):
        await reply_endpoints.reply_publish_endpoint(
            _request({"authorization": "Bearer tok"}),
            "a1",
            message="hi",
            session_id=None,
        )


async def test_unauthenticated_rejected(monkeypatch):
    from jvspatial.api.exceptions import AuthenticationError

    _patch_tokens(monkeypatch, bearer_uid=None, session_claims=None)
    _install_agent(monkeypatch, conversation=SimpleNamespace(user_id="alice"))

    with pytest.raises(AuthenticationError):
        await reply_endpoints.reply_subscribe_endpoint(
            _request({}), "a1", "s1", stream=False
        )
