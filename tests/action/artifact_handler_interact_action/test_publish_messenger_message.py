"""_publish_messenger_message uses the registered MessengerAdapter's live action."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.artifact_handler_interact_action import endpoints as ep


class _FakeInteraction:
    def add_parameter(self, *_a, **_k):
        return None

    def set_response(self, *_a, **_k):
        return None

    async def save(self):
        return self


class _FakeConversation:
    session_id = "sess-1"

    async def add_interaction(self, **_kwargs):
        return _FakeInteraction()


@pytest.mark.asyncio
async def test_publish_messenger_uses_registered_adapter_action(monkeypatch):
    """Happy path: use adapter.action.api().send_text_message, not find_one."""
    send_text = MagicMock(return_value={"message_id": "m1"})
    live_action = SimpleNamespace(
        is_configured=MagicMock(return_value=True),
        api=MagicMock(return_value=SimpleNamespace(send_text_message=send_text)),
    )
    adapter = SimpleNamespace(_initialized=True, action=live_action)
    response_bus = SimpleNamespace(_channel_adapters={"messenger": adapter})

    agent = SimpleNamespace(
        get_memory=AsyncMock(
            return_value=SimpleNamespace(
                get_user=AsyncMock(return_value=None),
            )
        ),
        get_response_bus=AsyncMock(return_value=response_bus),
        get_action_by_type=AsyncMock(side_effect=AssertionError("must not find_one")),
    )

    monkeypatch.setattr(
        "jvagent.memory.conversation.Conversation.get",
        AsyncMock(return_value=_FakeConversation()),
    )

    ok = await ep._publish_messenger_message(
        agent=agent,
        user_id="psid-1",
        session_id="sess-1",
        conversation_id="conv-1",
        content="Document ready.",
        display_doc="doc.pdf",
        job_id="job-1",
        answered=False,
    )
    assert ok is True
    send_text.assert_called_once_with("psid-1", "Document ready.")
    agent.get_action_by_type.assert_not_called()


@pytest.mark.asyncio
async def test_publish_messenger_cold_start_registers_adapter(monkeypatch):
    """When adapter missing, resolve token, register, then send via live action."""
    send_text = MagicMock(return_value={"message_id": "m2"})
    live_action = SimpleNamespace(
        is_configured=MagicMock(return_value=True),
        api=MagicMock(return_value=SimpleNamespace(send_text_message=send_text)),
        ensure_page_access_token=AsyncMock(return_value={"updated": True}),
        ensure_adapter_registered=AsyncMock(return_value=True),
    )
    adapters: dict = {}
    response_bus = SimpleNamespace(_channel_adapters=adapters)

    async def _register():
        adapters["messenger"] = SimpleNamespace(
            _initialized=True, action=live_action
        )
        return True

    live_action.ensure_adapter_registered = AsyncMock(side_effect=_register)

    agent = SimpleNamespace(
        get_memory=AsyncMock(
            return_value=SimpleNamespace(get_user=AsyncMock(return_value=None))
        ),
        get_response_bus=AsyncMock(return_value=response_bus),
        get_action_by_type=AsyncMock(return_value=live_action),
    )

    monkeypatch.setattr(
        "jvagent.memory.conversation.Conversation.get",
        AsyncMock(return_value=_FakeConversation()),
    )

    ok = await ep._publish_messenger_message(
        agent=agent,
        user_id="psid-2",
        session_id="sess-1",
        conversation_id="conv-1",
        content="Ready.",
        display_doc="x.png",
        job_id="job-2",
    )
    assert ok is True
    live_action.ensure_page_access_token.assert_awaited_once()
    live_action.ensure_adapter_registered.assert_awaited_once()
    send_text.assert_called_once_with("psid-2", "Ready.")
