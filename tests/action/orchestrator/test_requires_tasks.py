"""Declarative prerequisites + precondition registry (ADR-0026 TP4)."""

import uuid
from types import SimpleNamespace

import pytest

from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore
from jvagent.memory.task_graph import is_runnable, pick_top_runnable
from jvagent.action.orchestrator.preconditions import (
    clear_preconditions,
    evaluate_precondition,
    precondition_registered,
    register_precondition,
)
from jvagent.action.orchestrator.skills import SkillDoc, _parse_requires_tasks
from jvagent.action.orchestrator.skill_tasks import (
    _active_skill_task,
    push_followon_prerequisite,
    push_unmet_prerequisites,
)


def _sid():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


def test_parse_requires_tasks():
    parsed = _parse_requires_tasks(
        [
            {"when": "account_session", "push": "identity", "seed_from": "utterance"},
            {"bad": 1},  # dropped (no when/push)
            {"when": "x"},  # dropped (no push)
        ]
    )
    assert parsed == (
        {"when": "account_session", "push": "identity", "seed_from": ["utterance"]},
    )


@pytest.mark.asyncio
async def test_precondition_registry_fail_open():
    clear_preconditions()
    try:
        register_precondition("yes", lambda v: True)
        register_precondition("no", lambda v: False)
        assert precondition_registered("yes")
        assert await evaluate_precondition("yes", None) is True
        assert await evaluate_precondition("no", None) is False
        # Unregistered → fail-open (satisfied), no deadlock.
        assert await evaluate_precondition("unknown", None) is True
    finally:
        clear_preconditions()


@pytest.mark.asyncio
async def test_push_unmet_prerequisite_blocks_and_resumes(test_db):
    clear_preconditions()
    register_precondition("need_session", lambda v: False)  # unmet
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        visitor = SimpleNamespace(conversation=conv, utterance="track 1Z999")
        doc = SkillDoc(
            name="pre_alert",
            description="d",
            body="b",
            task_lock=True,
            requires_tasks=(
                {
                    "when": "need_session",
                    "push": "identity",
                    "seed_from": ["utterance"],
                },
            ),
        )

        pushed = await push_unmet_prerequisites(visitor, doc, [])
        assert pushed == "identity"

        store = TaskStore(conv)
        gated = _active_skill_task(store, "pre_alert")
        prereq = _active_skill_task(store, "identity")
        assert prereq is not None and gated is not None
        assert prereq.resumes == gated.id  # prerequisite resumes its parent
        assert prereq.id in gated.blocked_on  # parent blocked on the prerequisite
        assert gated.seed.get("utterance") == "track 1Z999"  # request preserved

        # The prerequisite owns the turn; the gated skill waits.
        assert is_runnable(store, gated) is False
        assert is_runnable(store, prereq) is True
        assert pick_top_runnable(store).id == prereq.id

        # One-time: re-pushing the same precondition is a no-op (no detour loop).
        assert await push_unmet_prerequisites(visitor, doc, []) is None

        # Completing the prerequisite unblocks the parent → it resumes.
        await prereq.complete()
        assert pick_top_runnable(TaskStore(conv)).id == gated.id
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_followon_prerequisite_blocks_parent_and_inherits_resume(test_db):
    """An internal hand-off (skill A → skill B) routed through the work graph: B
    blocks whatever A resumes (the gated parent) and inherits A's resume target, so
    the drain enters B before resuming the parent (ADR-0026)."""
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        store = TaskStore(conv)
        # The gated parent and the active hand-off skill that resumes it.
        parent = await store.create(
            title="gated",
            description="gated service",
            owner_action="gated_service",
            task_type="SKILL",
        )
        a = await store.create(
            title="a",
            description="skill a",
            owner_action="skill_a",
            task_type="SKILL",
            resumes=parent.id,
        )
        await a.start()

        pushed = await push_followon_prerequisite(
            visitor_for(conv), "skill_a", "skill_b"
        )
        assert pushed == "skill_b"

        store2 = TaskStore(conv)
        b = _active_skill_task(store2, "skill_b")
        parent2 = store2.get(parent.id)
        assert b is not None
        assert b.resumes == parent.id  # inherits A's resume target
        assert b.id in parent2.blocked_on  # parent now waits on B
    finally:
        await conv.delete(cascade=True)


def visitor_for(conv):
    return SimpleNamespace(conversation=conv, utterance="")


@pytest.mark.asyncio
async def test_satisfied_precondition_no_push(test_db):
    clear_preconditions()
    register_precondition("ok", lambda v: True)  # satisfied
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        visitor = SimpleNamespace(conversation=conv, utterance="hi")
        doc = SkillDoc(
            name="svc",
            description="d",
            body="b",
            task_lock=True,
            requires_tasks=({"when": "ok", "push": "prereq"},),
        )
        assert await push_unmet_prerequisites(visitor, doc, []) is None
        assert _active_skill_task(TaskStore(conv), "prereq") is None
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)
