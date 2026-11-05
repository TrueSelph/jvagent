"""Native subscriptions enforce the same current session proof as ingress."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.api.exceptions import AuthenticationError

from jvagent.action.interact.session_token import mint_session_token
from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.reply import endpoints
from jvagent.memory.conversation import Conversation


def install_native(
    monkeypatch: pytest.MonkeyPatch, conversation: Conversation
) -> tuple:
    bus = SimpleNamespace(
        _session_queues={"session": []},
        subscribe=AsyncMock(),
        unsubscribe=AsyncMock(),
        get_messages=AsyncMock(return_value=[]),
    )
    agent = SimpleNamespace(
        id="agent",
        get_actions=AsyncMock(return_value=[NativeHarnessAction()]),
        get_memory=AsyncMock(
            return_value=SimpleNamespace(
                get_conversation_by_session=AsyncMock(return_value=conversation)
            )
        ),
        get_response_bus=AsyncMock(return_value=bus),
        get_access_control_action=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(endpoints.Agent, "get", AsyncMock(return_value=agent))
    return agent, bus


@pytest.mark.asyncio
async def test_revoked_native_capability_cannot_read_reply_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JVSPATIAL_JWT_SECRET_KEY", "synthetic-native-subscription-signing-key-only"
    )
    conversation = Conversation(
        user_id="caller", session_id="session", public_identity_kind="anonymous"
    )
    token = mint_session_token(
        agent_id="agent",
        user_id="caller",
        session_id="session",
        token_secret=conversation.ensure_token_secret(),
    )
    conversation.rotate_token_secret()
    _, bus = install_native(monkeypatch, conversation)
    with pytest.raises(AuthenticationError):
        await endpoints.reply_subscribe_endpoint(
            SimpleNamespace(headers={"x-session-token": token}),
            "agent",
            "session",
            stream=False,
        )
    assert "session" in bus._session_queues


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["secret", "policy"])
async def test_native_stream_revalidates_before_delivering_a_frame(
    monkeypatch: pytest.MonkeyPatch, revocation: str
) -> None:
    from jvagent.action.response.message import ResponseMessage

    monkeypatch.setenv(
        "JVSPATIAL_JWT_SECRET_KEY", "synthetic-native-subscription-signing-key-only"
    )
    conversation = Conversation(
        user_id="caller", session_id="session", public_identity_kind="anonymous"
    )
    token = mint_session_token(
        agent_id="agent",
        user_id="caller",
        session_id="session",
        token_secret=conversation.ensure_token_secret(),
    )
    agent, bus = install_native(monkeypatch, conversation)
    bus.get_messages.return_value = [
        ResponseMessage(session_id="session", content="first")
    ]
    response = await endpoints.reply_subscribe_endpoint(
        SimpleNamespace(headers={"x-session-token": token}),
        "agent",
        "session",
        stream=True,
    )
    iterator = response.body_iterator
    assert "first" in await anext(iterator)
    if revocation == "secret":
        conversation.rotate_token_secret()
    else:
        agent.get_access_control_action.return_value = SimpleNamespace(
            policy_applies=lambda: True, has_action_access=AsyncMock(return_value=False)
        )
    callback = bus.subscribe.call_args.args[1]
    await callback(ResponseMessage(session_id="session", content="must not leak"))
    frame = await anext(iterator)
    assert "session_access_revoked" in frame and "must not leak" not in frame
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    bus.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_bearer_cannot_downgrade_to_anonymous_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jvspatial.api

    monkeypatch.setenv(
        "JVSPATIAL_JWT_SECRET_KEY", "synthetic-native-subscription-signing-key-only"
    )
    conversation = Conversation(
        user_id="caller", session_id="session", public_identity_kind="anonymous"
    )
    token = mint_session_token(
        agent_id="agent",
        user_id="caller",
        session_id="session",
        token_secret=conversation.ensure_token_secret(),
    )
    _, bus = install_native(monkeypatch, conversation)
    monkeypatch.setattr(
        jvspatial.api,
        "get_auth_service",
        lambda: SimpleNamespace(validate_token=AsyncMock(return_value=None)),
    )
    with pytest.raises(AuthenticationError):
        await endpoints.reply_subscribe_endpoint(
            SimpleNamespace(
                headers={"authorization": "Bearer invalid", "x-session-token": token}
            ),
            "agent",
            "session",
            stream=False,
        )
    assert "session" in bus._session_queues
