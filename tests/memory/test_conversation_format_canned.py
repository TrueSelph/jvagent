"""Tests for canned_response in Conversation._format_interactions / get_interaction_history."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation


def _session():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_format_history_includes_canned_before_response(test_db):
    """Assistant history shows transient canned lead-in then main response."""
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        ix = await conv.add_interaction(utterance="What is 2+2?")
        ix.canned_response = "Let me check that."
        ix.response = "Two plus two is four."
        await ix.save()

        hist = await conv.get_interaction_history(
            limit=1,
            excluded=False,
            formatted=True,
            max_statement_length=500,
        )
        assert [e.get("role") for e in hist] == ["user", "assistant", "assistant"]
        assert hist[0]["content"] == "What is 2+2?"
        assert hist[1]["content"] == "Let me check that."
        assert hist[2]["content"] == "Two plus two is four."
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_format_history_skips_canned_when_prefix_of_response(test_db):
    """No duplicate assistant turn when response already starts with canned text."""
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        ix = await conv.add_interaction(utterance="Hi")
        ix.canned_response = "One sec"
        ix.response = "One sec — here is the detail you asked for."
        await ix.save()

        hist = await conv.get_interaction_history(
            limit=1,
            excluded=False,
            formatted=True,
            max_statement_length=500,
        )
        assert [e.get("role") for e in hist] == ["user", "assistant"]
        assert hist[1]["content"].startswith("One sec")
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_format_history_canned_only_no_response(test_db):
    """Canned lead-in appears even before main response is persisted."""
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        ix = await conv.add_interaction(utterance="Hello")
        ix.canned_response = "Got it."
        await ix.save()

        hist = await conv.get_interaction_history(
            limit=1,
            excluded=False,
            formatted=True,
            max_statement_length=500,
        )
        assert [e.get("role") for e in hist] == ["user", "assistant"]
        assert hist[1]["content"] == "Got it."
    finally:
        await conv.delete(cascade=True)
