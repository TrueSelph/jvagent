"""``max_event_length``: bounding [EVENT] lines replayed into a loop prompt.

Interaction events are free-form log annotations with no length contract, so
``max_statement_length`` deliberately skips them (see ``_format_interactions``).
A caller that replays history on every model call — the Orchestrator loop with
``with_event`` on (ADR-0053) — needs an explicit bound, and must get it without
changing what any existing caller sees.
"""

import uuid

import pytest

from jvagent.memory.conversation import Conversation

LONG_EVENT = "Report form was sent to the user. " * 40


def _session() -> str:
    return f"test-sess-{uuid.uuid4().hex[:12]}"


async def _conv_with_event() -> Conversation:
    conv = await Conversation.create(
        session_id=_session(), user_id="user1", channel="default"
    )
    interaction = await conv.add_interaction(utterance="send the report")
    interaction.response = "Done."
    interaction.events.append({"action_name": "ReportAction", "content": LONG_EVENT})
    await interaction.save()
    return conv


@pytest.mark.asyncio
async def test_formatted_event_is_capped_and_untouched_without_the_knob(test_db):
    conv = await _conv_with_event()
    try:
        capped = await conv.get_interaction_history(
            limit=10, formatted=True, with_event=True, max_event_length=50
        )
        line = next(e for e in capped if e["content"].startswith("[EVENT]"))
        # "[EVENT] " prefix + 50 chars + the truncation marker.
        assert line["content"] == f"[EVENT] {LONG_EVENT[:50]}..."

        uncapped = await conv.get_interaction_history(
            limit=10, formatted=True, with_event=True
        )
        line = next(e for e in uncapped if e["content"].startswith("[EVENT]"))
        assert line["content"] == f"[EVENT] {LONG_EVENT}"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_statement_cap_still_does_not_touch_events(test_db):
    """The documented contract: a caller passing only ``max_statement_length``
    gets its utterances clipped and its events whole, exactly as before."""
    conv = await _conv_with_event()
    try:
        hist = await conv.get_interaction_history(
            limit=10, formatted=True, with_event=True, max_statement_length=10
        )
        line = next(e for e in hist if e["content"].startswith("[EVENT]"))
        assert line["content"] == f"[EVENT] {LONG_EVENT}"
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_raw_events_capped_without_mutating_the_stored_list(test_db):
    conv = await _conv_with_event()
    try:
        raw = await conv.get_interaction_history(
            limit=10, formatted=False, with_event=True, max_event_length=25
        )
        entry = next(e for e in raw if e.get("events"))
        assert entry["events"][0]["content"] == LONG_EVENT[:25] + "..."
        assert entry["events"][0]["action_name"] == "ReportAction"

        # The capped copy must not have rewritten what is persisted.
        again = await conv.get_interaction_history(
            limit=10, formatted=False, with_event=True
        )
        entry = next(e for e in again if e.get("events"))
        assert entry["events"][0]["content"] == LONG_EVENT
    finally:
        await conv.delete(cascade=True)
