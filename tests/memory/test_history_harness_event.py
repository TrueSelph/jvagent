"""``hide_correlation``: omit harness correlation from [EVENT] history."""

from __future__ import annotations

import uuid

import pytest

from jvagent.memory.conversation import Conversation

PRODUCT = "Report form was sent to the user."
HARNESS = "internal turn correlation (not a request_id): corr-abc123"


def _session() -> str:
    return f"test-sess-{uuid.uuid4().hex[:12]}"


async def _conv_with_mixed_events() -> Conversation:
    conv = await Conversation.create(
        session_id=_session(), user_id="user1", channel="default"
    )
    interaction = await conv.add_interaction(utterance="hello")
    interaction.response = "Hi."
    interaction.events.extend(
        [
            {"action_name": "harness", "content": HARNESS},
            {"action_name": "ReportAction", "content": PRODUCT},
        ]
    )
    await interaction.save()
    return conv


@pytest.mark.asyncio
async def test_with_event_omits_harness_by_default(test_db):
    conv = await _conv_with_mixed_events()
    try:
        hist = await conv.get_interaction_history(
            limit=10, formatted=True, with_event=True
        )
        events = [e["content"] for e in hist if e["content"].startswith("[EVENT]")]
        assert events == [f"[EVENT] {PRODUCT}"]
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_hide_correlation_false_keeps_correlation(test_db):
    conv = await _conv_with_mixed_events()
    try:
        hist = await conv.get_interaction_history(
            limit=10,
            formatted=True,
            with_event=True,
            hide_correlation=False,
        )
        events = [e["content"] for e in hist if e["content"].startswith("[EVENT]")]
        assert events == [f"[EVENT] {HARNESS}", f"[EVENT] {PRODUCT}"]
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_with_event_false_emits_no_events_even_when_correlation_shown(
    test_db,
):
    conv = await _conv_with_mixed_events()
    try:
        hist = await conv.get_interaction_history(
            limit=10,
            formatted=True,
            with_event=False,
            hide_correlation=False,
        )
        assert not any(e["content"].startswith("[EVENT]") for e in hist)
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_raw_history_also_filters_harness(test_db):
    conv = await _conv_with_mixed_events()
    try:
        raw = await conv.get_interaction_history(
            limit=10, formatted=False, with_event=True
        )
        entry = next(e for e in raw if e.get("events"))
        assert [ev["action_name"] for ev in entry["events"]] == ["ReportAction"]

        raw_keep = await conv.get_interaction_history(
            limit=10,
            formatted=False,
            with_event=True,
            hide_correlation=False,
        )
        entry = next(e for e in raw_keep if e.get("events"))
        assert [ev["action_name"] for ev in entry["events"]] == [
            "harness",
            "ReportAction",
        ]
    finally:
        await conv.delete(cascade=True)
