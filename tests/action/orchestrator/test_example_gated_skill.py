"""Non-zoon witness for ADR-0026 work-stack gating + plans.

Exercises the framework with the in-repo example skills (no tenant): a gated
booking skill whose frontmatter declares a `requires-tasks` prerequisite, plus a
demonstration that the same graph primitives drain an ordered multi-step plan.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.preconditions import (
    clear_preconditions,
    register_precondition,
)
from jvagent.action.orchestrator.skill_tasks import (
    _active_skill_task,
    push_unmet_prerequisites,
)
from jvagent.action.orchestrator.skills import SkillDoc, _parse_requires_tasks
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_graph import is_runnable, pick_top_runnable
from jvagent.memory.task_store import TaskStore

_EXAMPLES = (
    Path(__file__).resolve().parents[3]
    / "jvagent/action/interview/examples/example_account_gating"
)


def _sid():
    return f"test-sess-{uuid.uuid4().hex[:12]}"


def _skill_doc(folder: str) -> SkillDoc:
    """Build a SkillDoc straight from an example SKILL.md's frontmatter, the same
    way the resolver feeds requires-tasks into the orchestrator."""
    text = (_EXAMPLES / folder / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---"), "expected YAML frontmatter"
    fm = yaml.safe_load(text.split("---", 2)[1])
    return SkillDoc(
        name=fm["name"],
        description=fm["description"],
        body="",
        task_lock=bool(fm.get("task-lock")),
        requires_tasks=_parse_requires_tasks(fm.get("requires-tasks")),
    )


def test_example_frontmatter_declares_gate():
    booking = _skill_doc("example_booking_interview")
    assert booking.task_lock is True
    assert booking.requires_tasks == (
        {
            "when": "signed_in",
            "push": "example_signin_interview",
            "seed_from": ["utterance"],
        },
    )


@pytest.mark.asyncio
async def test_unmet_precondition_pushes_signin_and_resumes(test_db):
    clear_preconditions()
    register_precondition("signed_in", lambda v: False)  # no session
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        visitor = SimpleNamespace(conversation=conv, utterance="book a haircut friday")
        booking = _skill_doc("example_booking_interview")

        pushed = await push_unmet_prerequisites(visitor, booking, [])
        assert pushed == "example_signin_interview"

        store = TaskStore(conv)
        gated = _active_skill_task(store, "example_booking_interview")
        prereq = _active_skill_task(store, "example_signin_interview")
        assert prereq is not None and gated is not None
        assert prereq.resumes == gated.id
        assert prereq.id in gated.blocked_on
        assert gated.seed.get("utterance") == "book a haircut friday"
        assert is_runnable(store, gated) is False
        assert pick_top_runnable(store).id == prereq.id

        # Sign-in completes → the gated booking becomes the top runnable task.
        await prereq.complete()
        assert pick_top_runnable(TaskStore(conv)).id == gated.id
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_satisfied_precondition_runs_directly(test_db):
    clear_preconditions()
    register_precondition("signed_in", lambda v: True)  # already signed in
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        visitor = SimpleNamespace(conversation=conv, utterance="book a haircut")
        booking = _skill_doc("example_booking_interview")
        assert await push_unmet_prerequisites(visitor, booking, []) is None
        assert _active_skill_task(TaskStore(conv), "example_signin_interview") is None
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_plan_drains_in_order(test_db):
    """The same graph primitives express a multi-step plan: a parent blocked on an
    ordered chain of steps. pick_top_runnable walks them in order; each completion
    re-resolves the next runnable step — no plan-specific machinery."""
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        store = TaskStore(conv)
        plan = await store.create(
            title="plan", description="multi-step plan", task_type="plan"
        )
        steps = []
        prev = None
        for i in range(3):
            s = await store.create(
                title=f"step-{i}",
                description=f"step {i}",
                task_type="SKILL",
                order=i,
                blocked_on=[prev.id] if prev else [],
            )
            await s.start()
            steps.append(s)
            prev = s
        await plan.add_blocker(steps[-1].id)

        # Only step-0 is runnable; the chain unwinds one at a time, in order.
        seen = []
        for _ in range(3):
            top = pick_top_runnable(TaskStore(conv), task_types=["SKILL"])
            assert top is not None
            seen.append(top.title)
            await TaskStore(conv).get(top.id).complete()
        assert seen == ["step-0", "step-1", "step-2"]
    finally:
        await conv.delete(cascade=True)


def _non_task_lock_gated_skill() -> SkillDoc:
    """A capability skill (no turn-lock) that still declares a session prerequisite
    — the shape of a payment/tool skill whose tools need a customer session."""
    return SkillDoc(
        name="pay_capability",
        description="Live payment tools.",
        body="SOP.",
        task_lock=False,
        requires_tasks=(
            {
                "when": "signed_in",
                "push": "example_signin_interview",
                "seed_from": ["utterance"],
            },
        ),
    )


async def _apply_after_use_skill(ex, visitor, skill_docs, skill_name):
    """Drive _apply_task_lock_after_use_skill with _apply_active_task_lock_skill
    stubbed to record the doc it locks onto (isolates the gate decision)."""
    locked: dict = {}

    async def _fake_apply(doc, *a, **kw):
        locked["doc"] = doc
        return {"reply": object()}, {"reply"}, f"LOCKED:{doc.name}"

    ex._apply_active_task_lock_skill = _fake_apply  # type: ignore[assignment]
    result = await ex._apply_task_lock_after_use_skill(
        skill_name=skill_name,
        activation_obs=f"Activated skill '{skill_name}'",
        skill_docs=skill_docs,
        loop_actions=[],
        visitor=visitor,
        utterance=visitor.utterance,
        tools={},
        visible=set(),
        activated=[],
        observations=[],
    )
    return result, locked


@pytest.mark.asyncio
async def test_non_task_lock_skill_unmet_gate_pushes_prereq(test_db):
    """A non-turn-lock skill with requires-tasks whose precondition is UNMET must
    push the prerequisite and lock onto it — instead of proceeding ungated and
    leaving the model to narrate the missing session."""
    clear_preconditions()
    register_precondition("signed_in", lambda v: False)
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        pay = _non_task_lock_gated_skill()
        signin = _skill_doc("example_signin_interview")
        visitor = SimpleNamespace(conversation=conv, utterance="pay invoice Z1")
        ex = OrchestratorInteractAction()

        (doc, _tools, _vis, section, _detour), locked = await _apply_after_use_skill(
            ex, visitor, [pay, signin], "pay_capability"
        )

        # Gate fired: prerequisite pushed, and the lock is redirected onto it.
        store = TaskStore(conv)
        prereq = _active_skill_task(store, "example_signin_interview")
        gated = _active_skill_task(store, "pay_capability")
        assert prereq is not None and gated is not None
        assert prereq.resumes == gated.id
        assert gated.seed.get("utterance") == "pay invoice Z1"
        assert doc.name == "example_signin_interview"
        assert locked["doc"].name == "example_signin_interview"
        assert section == "LOCKED:example_signin_interview"
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_non_task_lock_skill_satisfied_gate_runs_unlocked(test_db):
    """When the precondition is already satisfied, the same skill proceeds on the
    normal unlocked surface — no prereq pushed, no lock applied."""
    clear_preconditions()
    register_precondition("signed_in", lambda v: True)
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        pay = _non_task_lock_gated_skill()
        visitor = SimpleNamespace(conversation=conv, utterance="pay invoice Z1")
        ex = OrchestratorInteractAction()

        (doc, _t, _v, section, _d), locked = await _apply_after_use_skill(
            ex, visitor, [pay], "pay_capability"
        )

        assert doc is None and section == ""
        assert "doc" not in locked  # never locked
        assert _active_skill_task(TaskStore(conv), "example_signin_interview") is None
    finally:
        clear_preconditions()
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_plain_skill_without_requires_is_untouched(test_db):
    """A plain skill (no turn-lock, no requires-tasks) is left entirely alone."""
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        faq = SkillDoc(name="faq", description="", body="SOP.", task_lock=False)
        visitor = SimpleNamespace(conversation=conv, utterance="where are you located")
        ex = OrchestratorInteractAction()

        (doc, _t, _v, section, _d), locked = await _apply_after_use_skill(
            ex, visitor, [faq], "faq"
        )

        assert doc is None and section == "" and "doc" not in locked
    finally:
        await conv.delete(cascade=True)
