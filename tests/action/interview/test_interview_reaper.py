"""ADR-0034 staleness reaper — nudge / abandon / expire on idle interview tasks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview import reaper
from jvagent.action.interview.reaper import (
    NUDGE_SENT_KEY,
    classify_reap_action,
    reap_interview_tasks,
)
from jvagent.action.interview.session import InterviewSession, save_session
from jvagent.action.interview.spec import parse_interview_spec
from jvagent.memory.task_store import TaskStore

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _spec(name, **policy):
    fields = [{"key": "tn", "prompt": "TN?", "required": True}]
    return parse_interview_spec(
        {"name": name, "fields": fields, **policy}, source_dir="", default_name=name
    )


def _lookup(specs):
    return lambda owner: specs.get(owner)


async def _store_with_task(owner, *, status="active", snapshot=None, blocked_on=None):
    conv = MagicMock()
    conv.tasks = []
    conv.context = {}
    conv.save = AsyncMock()
    store = TaskStore(conv)
    handle = await store.create(
        title=owner,
        description="d",
        owner_action=owner,
        task_type="SKILL",
        data={"interview_type": owner, "interview_managed": True},
    )
    await handle.start()
    if status == "parked":
        await handle.park(snapshot=snapshot or {})
    if blocked_on:
        for b in blocked_on:
            await handle.add_blocker(b)
    return conv, store, handle


# --- classify ----------------------------------------------------------------


def test_classify_thresholds():
    spec = _spec("t", nudge_after="4h", abandon_after="24h", parked_expire_after="30d")
    assert classify_reap_action("active", 3600, spec, False) is None
    assert classify_reap_action("active", 5 * 3600, spec, False) == "nudge"
    assert (
        classify_reap_action("active", 5 * 3600, spec, True) is None
    )  # already nudged
    assert classify_reap_action("active", 25 * 3600, spec, False) == "abandon"
    assert classify_reap_action("parked", 40 * 86400, spec, False) == "expire"
    # No TTLs declared → never reaped.
    bare = _spec("b")
    assert classify_reap_action("active", 10**9, bare, False) is None


# --- nudge -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nudge_sends_once_and_marks():
    spec = _spec("ti", nudge_after="4h", abandon_after="24h")
    conv, store, handle = await _store_with_task("ti")
    sent = []
    now = _NOW + timedelta(hours=5)

    counts = await reap_interview_tasks(
        conv, store, _lookup({"ti": spec}), now, send=lambda t: _append(sent, t)
    )
    assert counts["nudged"] == 1
    assert len(sent) == 1 and "ti" in sent[0].lower()
    assert store.get(handle.id).data.get(NUDGE_SENT_KEY) is True

    # A second sweep does not re-nudge (marked + updated_at refreshed).
    counts2 = await reap_interview_tasks(
        conv, store, _lookup({"ti": spec}), now, send=lambda t: _append(sent, t)
    )
    assert counts2["nudged"] == 0 and len(sent) == 1


async def _append(bucket, text):
    bucket.append(text)


# --- abandon -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_park_snapshots_and_parks():
    spec = _spec("tp", abandon_after="24h", on_abandon="park")
    conv, store, handle = await _store_with_task("tp")
    # A live session with collected state on the conversation.
    sess = InterviewSession(interview_type="tp")
    sess.set_value("tn", "1Z999")
    await save_session(conv, sess)

    counts = await reap_interview_tasks(
        conv, store, _lookup({"tp": spec}), _NOW + timedelta(hours=25)
    )
    assert counts["abandoned"] == 1
    parked = store.list(status="parked", owner_action="tp")
    assert len(parked) == 1
    assert parked[0].snapshot["fields"]["tn"] == "1Z999"


@pytest.mark.asyncio
async def test_abandon_cancel_closes_task():
    spec = _spec("tc", abandon_after="12h", on_abandon="cancel")
    conv, store, handle = await _store_with_task("tc")

    counts = await reap_interview_tasks(
        conv, store, _lookup({"tc": spec}), _NOW + timedelta(hours=13)
    )
    assert counts["abandoned"] == 1
    assert store.get(handle.id).status == "cancelled"


# --- expire ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_cancels_parked_task():
    spec = _spec("te", parked_expire_after="30d")
    conv, store, handle = await _store_with_task(
        "te", status="parked", snapshot={"interview_type": "te"}
    )

    counts = await reap_interview_tasks(
        conv, store, _lookup({"te": spec}), _NOW + timedelta(days=31)
    )
    assert counts["expired"] == 1
    assert store.get(handle.id).status == "cancelled"


# --- rails -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rail_skips_blocked_task():
    spec = _spec("tb", abandon_after="1h", on_abandon="cancel")
    conv, store, handle = await _store_with_task("tb", blocked_on=["prereq-1"])

    counts = await reap_interview_tasks(
        conv, store, _lookup({"tb": spec}), _NOW + timedelta(hours=5)
    )
    assert counts == {"nudged": 0, "abandoned": 0, "expired": 0}
    assert store.get(handle.id).status == "active"


@pytest.mark.asyncio
async def test_rail_skips_non_interview_task():
    conv = MagicMock()
    conv.tasks = []
    conv.context = {}
    conv.save = AsyncMock()
    store = TaskStore(conv)
    handle = await store.create(
        title="p", description="d", owner_action="SomeAction", task_type="PROACTIVE"
    )
    await handle.start()
    spec = _spec("SomeAction", abandon_after="1h", on_abandon="cancel")

    counts = await reap_interview_tasks(
        conv, store, _lookup({"SomeAction": spec}), _NOW + timedelta(hours=5)
    )
    assert counts["abandoned"] == 0
    assert store.get(handle.id).status == "active"
