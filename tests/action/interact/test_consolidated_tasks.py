"""The debug ``tasks`` array surfaces the FULL work graph (ADR-0026 observability)."""

import uuid

import pytest

from jvagent.action.interact.response_builder import (
    _consolidated_tasks_for_interaction,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore


@pytest.mark.asyncio
async def test_consolidated_tasks_show_full_graph_with_blocked_flag(test_db):
    conv = await Conversation.create(
        session_id=f"ct-{uuid.uuid4().hex[:8]}", user_id="u", channel="default"
    )
    try:
        st = TaskStore(conv)
        gated = await st.create(title="gated", description="d", task_type="SKILL")
        await gated.start()
        done = await st.create(title="done", description="d", task_type="SKILL")
        await done.start()
        await done.complete()
        prereq = await st.create(
            title="prereq", description="d", task_type="SKILL", resumes=gated.id
        )
        await prereq.start()
        await gated.add_blocker(prereq.id)
        dead = await st.create(title="dead", description="d", task_type="SKILL")
        await dead.start()
        await dead.cancel(reason="abandoned")

        active = conv.get_tasks(status="active")
        out = _consolidated_tasks_for_interaction(None, conv, active)
        by_title = {t["title"]: t for t in out}

        # Full graph: every status present, not just active + this-turn terminals.
        assert set(by_title) == {"gated", "done", "prereq", "dead"}
        assert by_title["done"]["status"] == "completed"
        assert by_title["dead"]["status"] == "cancelled"

        # Derived `blocked` flag distinguishes blocked-waiting from running/terminal.
        assert by_title["gated"]["blocked"] is True  # waits on prereq
        assert by_title["prereq"]["blocked"] is False  # runnable
        assert by_title["done"]["blocked"] is False  # terminal
        # Graph edges are carried through for inspection.
        assert by_title["prereq"]["resumes"] == gated.id
        assert prereq.id in by_title["gated"]["blocked_on"]
    finally:
        await conv.delete(cascade=True)
