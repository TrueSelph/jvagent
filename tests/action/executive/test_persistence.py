"""M7 (amended) — task-backed sustained activation across turns (ADR-0010 §2.5).

Sustained activation now lives on the conversation ``TaskStore`` as an
``executive_sustained`` task (not a context key). The reflex resumes from it; a
completing turn cancels it.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.contracts import ACTIVATE, RETURN, Brief, Result
from jvagent.action.executive.sustained import (
    SUSTAINED_TASK_TYPE,
    write_sustained,
)

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


def _sustained_tasks(conversation):
    from jvagent.memory.task_store import TaskStore

    return [
        th
        for th in TaskStore(conversation).list(status="active")
        if th.task_type == SUSTAINED_TASK_TYPE
    ]


async def test_sustained_activation_persisted_as_task(
    make_executive, make_visitor, stub_center, publish_log
):
    flow = stub_center(
        name="Interview", script=[RETURN(Result(content="Q1"), sustain=True)]
    )
    ex = make_executive(
        centers={"Interview": flow},
        executive_script=[
            ACTIVATE("Interview", brief=Brief(intent="interview"), on_done="voice")
        ],
    )
    visitor = make_visitor(utterance="start")
    await ex.execute(visitor)
    tasks = _sustained_tasks(visitor.conversation)
    assert len(tasks) == 1
    assert tasks[0].data["center"] == "Interview"
    visitor.conversation.save.assert_awaited()


async def test_sustained_resumes_next_turn(
    make_executive, make_visitor, stub_center, publish_log
):
    flow = stub_center(name="Interview", script=[RETURN(Result(content="Q2"))])
    ex = make_executive(centers={"Interview": flow}, executive_script=[])
    visitor = make_visitor(utterance="my answer")
    await write_sustained(
        visitor.conversation,
        center="Interview",
        brief={"intent": "interview", "slots": {}, "constraints": []},
    )
    await ex.execute(visitor)
    assert _contents(publish_log) == ["Q2"]
    assert flow.call_count == 1


async def test_completing_flow_cancels_sustained_task(
    make_executive, make_visitor, stub_center, publish_log
):
    flow = stub_center(
        name="Interview", script=[RETURN(Result(content="All done!"), sustain=False)]
    )
    ex = make_executive(centers={"Interview": flow}, executive_script=[])
    visitor = make_visitor(utterance="final answer")
    await write_sustained(
        visitor.conversation, center="Interview", brief={"intent": "interview"}
    )
    await ex.execute(visitor)
    assert _contents(publish_log) == ["All done!"]
    assert _sustained_tasks(visitor.conversation) == []  # cancelled on completion


async def test_no_sustained_task_when_not_locking(
    make_executive, make_visitor, publish_log
):
    ex = make_executive(executive_script=[])  # cognition yields (no script)
    visitor = make_visitor(utterance="hello")
    await ex.execute(visitor)
    assert _sustained_tasks(visitor.conversation) == []
