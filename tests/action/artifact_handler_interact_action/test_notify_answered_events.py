"""Notify publish records answered-pending events on the proactive interaction."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.artifact_handler_interact_action import endpoints as ep

_DOC_NAME = "o.User.abc_photo.jpeg"
_PENDING_Q = "what is the first item in the photo"


class _FakeInteraction:
    def __init__(self):
        self.events = []

    def add_parameter(self, *_a, **_k):
        return None

    def set_response(self, *_a, **_k):
        return None

    def add_event(self, event, action_name):
        self.events.append({"action_name": action_name, "content": event})
        return True

    async def save(self):
        return self


class _FakeConversation:
    session_id = "sess-1"

    def __init__(self):
        self.interaction = _FakeInteraction()

    async def add_interaction(self, **_kwargs):
        return self.interaction


@pytest.mark.asyncio
async def test_publish_whatsapp_records_answered_event(monkeypatch):
    conv = _FakeConversation()
    send = AsyncMock(return_value={"ok": True})
    agent = SimpleNamespace(
        get_memory=AsyncMock(
            return_value=SimpleNamespace(get_user=AsyncMock(return_value=None))
        ),
        get_action_by_type=AsyncMock(
            return_value=SimpleNamespace(
                is_configured=MagicMock(return_value=True),
                api=AsyncMock(return_value=SimpleNamespace(send_message=send)),
            )
        ),
    )
    monkeypatch.setattr(
        "jvagent.memory.conversation.Conversation.get",
        AsyncMock(return_value=conv),
    )
    ok = await ep._publish_whatsapp_message(
        agent=agent,
        user_id="5920000000",
        session_id="sess-1",
        conversation_id="conv-1",
        content="Your image is ready. The first item is a bottle.",
        display_doc="photo.jpeg",
        job_id="job-1",
        answered=True,
        internal_doc_name=_DOC_NAME,
        pending_question=_PENDING_Q,
    )
    assert ok is True
    contents = [e["content"] for e in conv.interaction.events]
    assert any(_DOC_NAME in c and _PENDING_Q in c for c in contents)
    assert any(c.startswith("Answered pending question") for c in contents)


@pytest.mark.asyncio
async def test_publish_messenger_records_answered_event(monkeypatch):
    conv = _FakeConversation()
    send_text = MagicMock(return_value={"message_id": "m1"})
    live_action = SimpleNamespace(
        is_configured=MagicMock(return_value=True),
        api=MagicMock(return_value=SimpleNamespace(send_text_message=send_text)),
    )
    adapter = SimpleNamespace(_initialized=True, action=live_action)
    agent = SimpleNamespace(
        get_memory=AsyncMock(
            return_value=SimpleNamespace(get_user=AsyncMock(return_value=None))
        ),
        get_response_bus=AsyncMock(
            return_value=SimpleNamespace(_channel_adapters={"messenger": adapter})
        ),
        get_action_by_type=AsyncMock(side_effect=AssertionError("must not find_one")),
    )
    monkeypatch.setattr(
        "jvagent.memory.conversation.Conversation.get",
        AsyncMock(return_value=conv),
    )
    ok = await ep._publish_messenger_message(
        agent=agent,
        user_id="psid-1",
        session_id="sess-1",
        conversation_id="conv-1",
        content="Your PDF is ready.",
        display_doc="doc.pdf",
        job_id="job-1",
        answered=True,
        internal_doc_name="user_doc.pdf",
        pending_question="what is item two",
    )
    assert ok is True
    contents = [e["content"] for e in conv.interaction.events]
    assert any("user_doc.pdf" in c and "item two" in c for c in contents)
