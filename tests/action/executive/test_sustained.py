"""Unit tests for task-backed sustained activation (ADR-0010 §2.5)."""

from __future__ import annotations

import pytest

from jvagent.action.executive.registry import Capability, CapabilityRegistry
from jvagent.action.executive.sustained import (
    SUSTAINED_TASK_TYPE,
    clear_sustained,
    has_active_ia_task,
    read_sustained,
    write_sustained,
)

pytestmark = pytest.mark.asyncio


class _FakeConv:
    """Minimal conversation double — TaskStore reads/writes ``.tasks``."""

    def __init__(self):
        self.tasks = []
        self.saved = 0

    async def save(self):
        self.saved += 1


def _active_sustained(conv):
    from jvagent.memory.task_store import TaskStore

    return [
        t
        for t in TaskStore(conv).list(status="active")
        if t.task_type == SUSTAINED_TASK_TYPE
    ]


async def test_write_read_clear_roundtrip():
    conv = _FakeConv()
    await write_sustained(conv, center="SkillsCenter", brief={"intent": "x"})
    got = await read_sustained(conv, {"SkillsCenter"}, None)
    assert got is not None
    assert got["center"] == "SkillsCenter"
    assert got["brief"]["intent"] == "x"

    await clear_sustained(conv)
    assert await read_sustained(conv, {"SkillsCenter"}, None) is None


async def test_write_is_idempotent_single_task():
    conv = _FakeConv()
    await write_sustained(conv, center="A", brief={"intent": "1"})
    await write_sustained(conv, center="A", brief={"intent": "2"})
    active = _active_sustained(conv)
    assert len(active) == 1
    assert active[0].data["brief"]["intent"] == "2"  # updated in place


async def test_read_ignores_center_not_loaded():
    conv = _FakeConv()
    await write_sustained(conv, center="SkillsCenter", brief={"intent": "x"})
    # SkillsCenter not in the loaded set → not resumable.
    assert await read_sustained(conv, {"IACenter"}, None) is None


async def test_pass2_resumes_from_ia_owned_task():
    from jvagent.memory.task_store import TaskStore

    conv = _FakeConv()
    handle = await TaskStore(conv).create(
        title="signup interview",
        description="d",
        owner_action="SignupInterviewInteractAction",
        task_type="INTERVIEW",
    )
    await handle.start()

    registry = CapabilityRegistry(
        [
            Capability(
                id="SignupInterviewInteractAction",
                kind="ia",
                center="IACenter",
                handle="SignupInterviewInteractAction",
            )
        ]
    )
    got = await read_sustained(conv, {"IACenter"}, registry)
    assert got is not None
    assert got["center"] == "IACenter"
    assert got["brief"]["slots"]["capability"] == "SignupInterviewInteractAction"
    assert await has_active_ia_task(conv, {"IACenter"}, registry) is True


async def test_pass2_ignored_when_handling_center_absent():
    from jvagent.memory.task_store import TaskStore

    conv = _FakeConv()
    handle = await TaskStore(conv).create(
        title="signup", description="d", owner_action="SignupInterviewInteractAction"
    )
    await handle.start()
    registry = CapabilityRegistry(
        [Capability(id="SignupInterviewInteractAction", kind="ia", center="IACenter")]
    )
    # IACenter not loaded → no resume, no active-IA-task signal.
    assert await read_sustained(conv, {"SkillsCenter"}, registry) is None
    assert await has_active_ia_task(conv, {"SkillsCenter"}, registry) is False


async def test_no_conversation_is_safe():
    assert await read_sustained(None, {"X"}, None) is None
    assert await has_active_ia_task(None, {"X"}, None) is False
    await write_sustained(None, center="X", brief={})  # no raise
    await clear_sustained(None)  # no raise
