"""Orchestrator resumable-plan persistence (ADR-0019).

Covers the opt-in ``update_plan`` tool, the ``AGENTIC_LOOP`` control-task it
writes, soft resume via ``active_plan``/``plan_resume_note``, the
complete-or-park lifecycle, and the default-off zero-cost guarantee.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from jvagent.action.orchestrator.continuation import active_plan, plan_resume_note
from jvagent.action.orchestrator.core_tools import build_plan_tool
from jvagent.memory.task_store import TaskStore, normalize_step_status


# --------------------------------------------------------------------------- #
# A minimal in-memory conversation: TaskStore needs only ``tasks`` + ``save``.
# --------------------------------------------------------------------------- #
class FakeConversation:
    def __init__(self) -> None:
        self.tasks: List[Dict[str, Any]] = []
        self.saves = 0

    async def save(self) -> None:
        self.saves += 1


def _visitor(conv: FakeConversation) -> Any:
    return SimpleNamespace(conversation=conv, tasks=TaskStore(conv))


class _Action:
    """Stand-in carrying the owner_action identity the tool stamps on the task."""

    @staticmethod
    def get_class_name() -> str:
        return "OrchestratorInteractAction"


# --------------------------------------------------------------------------- #
# normalize_step_status + sync_plan
# --------------------------------------------------------------------------- #
def test_normalize_step_status_aliases() -> None:
    assert normalize_step_status("completed") == "done"
    assert normalize_step_status("in progress") == "in_progress"
    assert normalize_step_status("TODO") == "pending"
    assert normalize_step_status("blocked") == "failed"
    assert normalize_step_status("nonsense") == "pending"


@pytest.mark.asyncio
async def test_sync_plan_overwrites_and_honors_statuses() -> None:
    conv = FakeConversation()
    store = TaskStore(conv)
    handle = await store.create(
        title="t", description="t", task_type="AGENTIC_LOOP", owner_action="O"
    )
    await handle.start()
    await handle.sync_plan(
        [
            {"description": "Fetch data", "status": "done"},
            {"step": "Summarize", "status": "in_progress"},
            "Write report",  # bare string ignored by sync_plan (needs a dict)
        ]
    )
    fresh = store.get(handle.id)
    steps = fresh.list_steps()
    # The bare string is skipped (sync_plan takes dict items); two steps remain.
    assert [s.description for s in steps] == ["Fetch data", "Summarize"]
    assert steps[0].status == "done"
    assert steps[1].status == "in_progress"
    # Full-state overwrite: a second call replaces the plan entirely.
    await fresh.sync_plan([{"description": "Only step", "status": "pending"}])
    assert [s.description for s in store.get(handle.id).list_steps()] == ["Only step"]


# --------------------------------------------------------------------------- #
# update_plan tool
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_plan_creates_agentic_loop_task() -> None:
    conv = FakeConversation()
    tool = build_plan_tool(_Action(), _visitor(conv))
    assert tool.name == "update_plan"

    out = await tool.run({"steps": ["Fetch", "Summarize", "Write"]})
    assert "Fetch" in out
    assert len(conv.tasks) == 1
    task = conv.tasks[0]
    assert task["task_type"] == "AGENTIC_LOOP"
    assert task["owner_action"] == "OrchestratorInteractAction"
    assert task["status"] == "active"
    assert [s["description"] for s in task["steps"]] == ["Fetch", "Summarize", "Write"]


@pytest.mark.asyncio
async def test_update_plan_reconciles_single_active_plan() -> None:
    conv = FakeConversation()
    tool = build_plan_tool(_Action(), _visitor(conv))
    await tool.run({"steps": ["A", "B"]})
    await tool.run(
        {"steps": [{"step": "A", "status": "done"}, {"step": "B", "status": "done"}]}
    )
    # Still exactly one task (overwrite, not a second plan).
    assert len(conv.tasks) == 1
    assert all(s["status"] == "done" for s in conv.tasks[0]["steps"])


@pytest.mark.asyncio
async def test_update_plan_requires_steps() -> None:
    conv = FakeConversation()
    tool = build_plan_tool(_Action(), _visitor(conv))
    out = await tool.run({})
    assert "needs a non-empty" in out
    assert conv.tasks == []


# --------------------------------------------------------------------------- #
# active_plan + plan_resume_note
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_active_plan_finds_only_agentic_loop() -> None:
    conv = FakeConversation()
    store = TaskStore(conv)
    # An interview control-task must NOT be seen as an orchestrator plan.
    iv = await store.create(
        title="iv", description="iv", task_type="INTERVIEW", owner_action="Iv"
    )
    await iv.start()
    assert active_plan(_visitor(conv), owner="OrchestratorInteractAction") is None

    plan = await store.create(
        title="p",
        description="p",
        task_type="AGENTIC_LOOP",
        owner_action="OrchestratorInteractAction",
    )
    await plan.start()
    found = active_plan(_visitor(conv), owner="OrchestratorInteractAction")
    assert found is not None and found.id == plan.id


@pytest.mark.asyncio
async def test_plan_resume_note_only_when_pending() -> None:
    conv = FakeConversation()
    tool = build_plan_tool(_Action(), _visitor(conv))
    await tool.run({"steps": [{"step": "A", "status": "done"}, {"step": "B"}]})
    handle = active_plan(_visitor(conv), owner="OrchestratorInteractAction")
    note = plan_resume_note(handle)
    assert "still in progress" in note
    assert "do NOT redo completed steps" in note

    # All done → no resume note.
    await tool.run({"steps": [{"step": "A", "status": "done"}]})
    handle2 = active_plan(_visitor(conv), owner="OrchestratorInteractAction")
    assert plan_resume_note(handle2) == ""


# --------------------------------------------------------------------------- #
# Lifecycle: _finalize_plan (complete-or-park) + default-off zero cost
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_finalize_completes_done_plan_and_parks_pending() -> None:
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    conv = FakeConversation()
    ex = OrchestratorInteractAction()
    ex.planning = True
    tool = build_plan_tool(ex, _visitor(conv))

    # Pending steps remain → finalize leaves the plan ACTIVE (parked to resume).
    await tool.run({"steps": [{"step": "A", "status": "done"}, {"step": "B"}]})
    await ex._finalize_plan(_visitor(conv))
    assert active_plan(_visitor(conv), owner=ex.get_class_name()) is not None

    # All steps terminal → finalize completes and clears the plan.
    await tool.run({"steps": [{"step": "A", "status": "done"}]})
    await ex._finalize_plan(_visitor(conv))
    assert active_plan(_visitor(conv), owner=ex.get_class_name()) is None
    assert conv.tasks == []  # deleted on completion


@pytest.mark.asyncio
async def test_planning_off_is_zero_cost() -> None:
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    ex = OrchestratorInteractAction()
    assert ex.planning is False  # default off

    conv = FakeConversation()
    # _finalize_plan is a no-op when planning is off — never touches the store.
    await ex._finalize_plan(_visitor(conv))
    assert conv.saves == 0
    assert conv.tasks == []
