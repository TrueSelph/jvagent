"""Tests for proactive task eligibility engine."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_eligibility import (
    are_prerequisites_met,
    conversation_has_blockers,
    is_event_eligible,
    is_schedule_eligible,
    pick_next_proactive_task,
)
from jvagent.memory.task_proactive import ProactiveTaskSpec
from jvagent.memory.task_store import TaskStore


def _make_conversation():
    conv = MagicMock()
    conv.tasks = []
    conv.save = AsyncMock()
    return conv


@pytest.mark.asyncio
async def test_schedule_eligible_respects_not_before_and_not_after():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    spec = ProactiveTaskSpec(
        directive="ping",
        not_before="2026-06-01T11:00:00+00:00",
        not_after="2026-06-01T13:00:00+00:00",
    )
    assert is_schedule_eligible(spec, now) is True
    assert (
        is_schedule_eligible(
            spec,
            datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        )
        is False
    )


@pytest.mark.asyncio
async def test_pick_next_honors_priority_then_fifo():
    conv = _make_conversation()
    store = TaskStore(conv)
    low = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="low", priority=0),
        title="low",
    )
    high = await store.enqueue_proactive(
        ProactiveTaskSpec(directive="high", priority=5),
        title="high",
    )
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    picked = pick_next_proactive_task(store, now=now)
    assert picked is not None
    assert picked.id == high.id
    assert low.status == "pending"


@pytest.mark.asyncio
async def test_pick_next_skips_when_skill_blocker_active():
    conv = _make_conversation()
    store = TaskStore(conv)
    skill = await store.create(
        title="skill session",
        description="skill",
        task_type="SKILL",
        owner_action="use_skill",
    )
    await skill.start()
    await store.enqueue_proactive(
        ProactiveTaskSpec(directive="blocked"),
        title="blocked",
    )
    picked = pick_next_proactive_task(
        store,
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert picked is None
    assert conversation_has_blockers(store) is True


@pytest.mark.asyncio
async def test_event_eligible_keyword_trigger():
    spec = ProactiveTaskSpec(
        directive="follow up",
        trigger_on="keyword",
        trigger_keyword="busy",
    )
    interaction = SimpleNamespace(utterance="I'm busy now", inner_monologue="")
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert is_event_eligible(spec, interaction, now=now) is True


@pytest.mark.asyncio
async def test_prerequisites_must_be_completed():
    conv = _make_conversation()
    store = TaskStore(conv)
    prereq = await store.create(title="prereq", description="prereq")
    await prereq.start()
    spec = ProactiveTaskSpec(directive="after", requires_tasks=[prereq.id])
    assert are_prerequisites_met(store, spec) is False
    await prereq.complete()
    assert are_prerequisites_met(store, spec) is True
