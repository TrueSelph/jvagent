"""Regression test for proactive-interaction history serialization.

When an Interaction has an empty utterance (the shape produced by
Agent.send_proactive_message), Conversation._format_interactions must NOT
inject a blank ``role: "user"`` entry into the LLM history. The entry should
appear as a standalone assistant turn.
"""

import uuid

import pytest

from jvagent.memory.conversation import Conversation


def _session() -> str:
    return f"test-sess-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_empty_utterance_interaction_omits_user_role(test_db):
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        # Proactive: empty utterance, only response.
        proactive = await conv.add_interaction(utterance="")
        proactive.response = "Proactive ping from agent."
        await proactive.save()

        # Normal: user utterance + assistant response.
        normal = await conv.add_interaction(utterance="Got it, thanks!")
        normal.response = "You're welcome."
        await normal.save()

        hist = await conv.get_interaction_history(
            limit=10,
            excluded=False,
            formatted=True,
            max_statement_length=500,
        )

        roles = [e.get("role") for e in hist]
        assert roles == ["assistant", "user", "assistant"]
        assert hist[0]["content"] == "Proactive ping from agent."
        assert hist[1]["content"] == "Got it, thanks!"
        assert hist[2]["content"] == "You're welcome."
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_whitespace_only_utterance_also_omits_user_role(test_db):
    """Whitespace-only utterances are treated the same as empty."""
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        ix = await conv.add_interaction(utterance="   \n\t  ")
        ix.response = "Standalone assistant turn."
        await ix.save()

        hist = await conv.get_interaction_history(
            limit=1,
            excluded=False,
            formatted=True,
            max_statement_length=500,
        )

        assert [e.get("role") for e in hist] == ["assistant"]
        assert hist[0]["content"] == "Standalone assistant turn."
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_with_utterance_false_unaffected_by_guard(test_db):
    """When the caller asks not to include utterances, behavior is unchanged."""
    conv = await Conversation.create(
        session_id=_session(),
        user_id="user1",
        channel="default",
    )
    try:
        proactive = await conv.add_interaction(utterance="")
        proactive.response = "P."
        await proactive.save()

        normal = await conv.add_interaction(utterance="hi")
        normal.response = "hello."
        await normal.save()

        hist = await conv.get_interaction_history(
            limit=10,
            excluded=False,
            with_utterance=False,
            formatted=True,
            max_statement_length=500,
        )

        assert [e.get("role") for e in hist] == ["assistant", "assistant"]
        assert hist[0]["content"] == "P."
        assert hist[1]["content"] == "hello."
    finally:
        await conv.delete(cascade=True)
