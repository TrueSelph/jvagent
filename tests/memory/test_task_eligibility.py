"""Tests for proactive task eligibility engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.memory.task_eligibility import (
    are_prerequisites_met,
    blocker_stale_seconds,
    conversation_has_blockers,
    is_event_eligible,
    is_schedule_eligible,
    parse_instant,
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


# --- orphaned-blocker lease (M11) ---------------------------------------------


async def _skill_store_with_pending_proactive():
    conv = _make_conversation()
    store = TaskStore(conv)
    skill = await store.create(
        title="skill session",
        description="skill",
        task_type="SKILL",
        owner_action="use_skill",
    )
    await skill.start()  # active blocker; updated_at ~= now
    await store.enqueue_proactive(
        ProactiveTaskSpec(directive="ping", priority=1), title="ping"
    )
    return store, skill


@pytest.mark.asyncio
async def test_fresh_skill_blocker_suppresses_proactive():
    store, skill = await _skill_store_with_pending_proactive()
    fresh = parse_instant(skill.updated_at)
    assert conversation_has_blockers(store, now=fresh) is True
    assert pick_next_proactive_task(store, now=fresh) is None


@pytest.mark.asyncio
async def test_stale_skill_blocker_stops_suppressing_proactive():
    """An orphaned SKILL blocker past its lease must not suppress proactive
    dispatch forever."""
    store, skill = await _skill_store_with_pending_proactive()
    stale = parse_instant(skill.updated_at) + timedelta(days=2)  # past 24h lease
    assert conversation_has_blockers(store, now=stale) is False
    picked = pick_next_proactive_task(store, now=stale)
    assert picked is not None
    # The orphaned SKILL task is NOT cancelled — only the suppression lifts.
    assert skill.status == "active"


@pytest.mark.asyncio
async def test_lease_zero_disables_staleness(monkeypatch):
    monkeypatch.setenv("JVAGENT_TASK_BLOCKER_STALE_SECONDS", "0")
    store, skill = await _skill_store_with_pending_proactive()
    stale = parse_instant(skill.updated_at) + timedelta(days=365)
    # Lease disabled → blocker suppresses regardless of age (legacy behavior).
    assert conversation_has_blockers(store, now=stale) is True


def test_blocker_stale_seconds_env_parsing(monkeypatch):
    monkeypatch.delenv("JVAGENT_TASK_BLOCKER_STALE_SECONDS", raising=False)
    assert blocker_stale_seconds() == 24 * 60 * 60
    monkeypatch.setenv("JVAGENT_TASK_BLOCKER_STALE_SECONDS", "120")
    assert blocker_stale_seconds() == 120
    monkeypatch.setenv("JVAGENT_TASK_BLOCKER_STALE_SECONDS", "0")
    assert blocker_stale_seconds() == 0
    monkeypatch.setenv("JVAGENT_TASK_BLOCKER_STALE_SECONDS", "bogus")
    assert blocker_stale_seconds() == 24 * 60 * 60
