"""ADR-0034 L5 — two-strike soft-abandon at the companion gate.

Covers the deterministic strike accounting (increments + engagement reset), the
park/cancel application that reuses the reaper shape, and the loop wiring: the
streak-2 one-turn ask and the streak-3 apply-``on_abandon``-then-route.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as sei
from jvagent.action.orchestrator import continuation
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.action.interview.spec import InterviewSpec

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def _visitor_with_context(ctx=None):
    conversation = MagicMock()
    conversation.context = dict(ctx or {})
    conversation.save = AsyncMock()
    visitor = MagicMock()
    visitor.conversation = conversation
    return visitor


class _FakeHandle:
    def __init__(self):
        self.parked = None
        self.cancelled = None

    async def park(self, *, snapshot=None, reason=""):
        self.parked = {"snapshot": snapshot, "reason": reason}

    async def cancel(self, *, reason=""):
        self.cancelled = {"reason": reason}


class _FakeTaskStore:
    def __init__(self, handle):
        self._handle = handle

    def list(self, *, status=None, owner_action=None):
        return [self._handle] if self._handle is not None else []


def _agent_with_spec(spec):
    interview_action = SimpleNamespace(
        _registry=SimpleNamespace(get=lambda name: spec if name == spec.name else None),
        _ensure_specs_loaded=AsyncMock(),
    )
    agent = MagicMock()
    agent.get_action_by_type = AsyncMock(return_value=interview_action)
    agent.get_access_control_action = AsyncMock(return_value=None)
    return agent


# --------------------------------------------------------------------------- #
# 1. Strike increments
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_note_soft_abandon_strike_increments_when_no_engagement():
    v = _visitor_with_context()

    s1 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=2)
    s2 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=2)
    s3 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=2)

    assert (s1, s2, s3) == (1, 2, 3)
    v.conversation.save.assert_awaited()


# --------------------------------------------------------------------------- #
# 2. Reset on engagement (collected count grew between strikes)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_note_soft_abandon_strike_resets_when_interview_engaged():
    v = _visitor_with_context()

    s1 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=1)
    s2 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=1)
    # User answered a field between off-topic attempts: collected grew 1 -> 3.
    s3 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=3)
    # Then another off-topic attempt with no further engagement.
    s4 = await continuation.note_soft_abandon_strike(v, "signup", collected_count=3)

    assert (s1, s2, s3, s4) == (1, 2, 1, 2)


# --------------------------------------------------------------------------- #
# 3. apply_soft_abandon — park + cancel shapes (real reaper reuse)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_apply_soft_abandon_parks_and_snapshots():
    handle = _FakeHandle()
    spec = InterviewSpec(name="signup", title="Signup", on_abandon="park")
    v = _visitor_with_context(
        {
            "interview": {
                "interview_type": "signup",
                "status": "active",
                "fields": {"user_name": "Eldon"},
            }
        }
    )
    v.tasks = _FakeTaskStore(handle)

    applied = await continuation.apply_soft_abandon(v, _agent_with_spec(spec), "signup")

    assert applied is True
    assert handle.parked is not None
    assert handle.parked["snapshot"]["fields"] == {"user_name": "Eldon"}
    # live interview scratch cleared
    assert "interview" not in v.conversation.context


@pytest.mark.asyncio
async def test_apply_soft_abandon_cancels():
    handle = _FakeHandle()
    spec = InterviewSpec(name="otp", title="Verify", on_abandon="cancel")
    v = _visitor_with_context(
        {"interview": {"interview_type": "otp", "status": "active", "fields": {}}}
    )
    v.tasks = _FakeTaskStore(handle)

    applied = await continuation.apply_soft_abandon(v, _agent_with_spec(spec), "otp")

    assert applied is True
    assert handle.cancelled is not None
    assert handle.parked is None
    assert "interview" not in v.conversation.context


@pytest.mark.asyncio
async def test_apply_soft_abandon_returns_false_without_task():
    spec = InterviewSpec(name="signup", title="Signup", on_abandon="park")
    v = _visitor_with_context()
    v.tasks = _FakeTaskStore(None)

    applied = await continuation.apply_soft_abandon(v, _agent_with_spec(spec), "signup")

    assert applied is False


# --------------------------------------------------------------------------- #
# Loop wiring — streak-2 ask and streak-3 route
# --------------------------------------------------------------------------- #


def _locked_doc():
    return SkillDoc(
        name="signup_interview",
        description="Signup.",
        body="SOP.",
        task_lock=True,
    )


def _target_doc():
    return SkillDoc(
        name="faq_lookup",
        description="Answer product questions.",
        body="",
    )


def _wire_locked_loop(monkeypatch, ex, locked, target, spec):
    """Resolve the locked skill + a trivial locked surface without booting a skill."""

    async def _find_lock(self, visitor, skill_docs, actions):
        return locked

    monkeypatch.setattr(
        OrchestratorInteractAction, "_find_active_task_lock_skill_doc", _find_lock
    )

    async def _apply_lock(self, skill_doc, *a, **kw):
        # (tools, visible, skills_section) — leave the assembled surface intact.
        tools = a[3] if len(a) > 3 else kw.get("tools")
        visible = a[4] if len(a) > 4 else kw.get("visible")
        return tools, visible, ""

    monkeypatch.setattr(
        OrchestratorInteractAction, "_apply_active_task_lock_skill", _apply_lock
    )
    monkeypatch.setattr(
        OrchestratorInteractAction,
        "_discover_skills",
        lambda self, _agent: [locked, target],
    )
    monkeypatch.setattr(sei, "active_flow_owner", lambda v, **kw: None)


@pytest.mark.asyncio
async def test_gate_streak_two_composes_ask(
    make_orchestrator, make_visitor, monkeypatch
):
    locked, target = _locked_doc(), _target_doc()
    spec = InterviewSpec(name="signup_interview", title="signup", on_abandon="park")

    ex = make_orchestrator(actions=[], decisions=[], agent=_agent_with_spec(spec))
    ex.lock_active_flow = True
    _wire_locked_loop(monkeypatch, ex, locked, target, spec)

    monkeypatch.setattr(
        continuation, "note_soft_abandon_strike", AsyncMock(return_value=2)
    )
    apply_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(continuation, "apply_soft_abandon", apply_spy)

    captured: list = []
    decisions = [
        {"action": "tool", "tool": "use_skill", "args": {"name": "faq_lookup"}},
        {"action": "final", "answer": ""},
    ]

    async def _spy(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        captured.append([dict(o) for o in observations])
        return decisions[min(len(captured) - 1, len(decisions) - 1)]

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _spy)

    v = make_visitor(utterance="what are your hours")
    await ex.execute(v)

    # streak 2 does not abandon
    apply_spy.assert_not_awaited()
    # the one-turn ask reaches the model as gate framing
    gate_obs = [o for turn in captured for o in turn if o.get("tool") == "use_skill"]
    assert gate_obs
    text = gate_obs[-1]["observation"]
    assert "set aside the signup" in text
    assert "help with that instead" in text


@pytest.mark.asyncio
async def test_gate_streak_three_abandons_and_routes(
    make_orchestrator, make_visitor, monkeypatch
):
    locked, target = _locked_doc(), _target_doc()
    spec = InterviewSpec(name="signup_interview", title="signup", on_abandon="park")

    ex = make_orchestrator(actions=[], decisions=[], agent=_agent_with_spec(spec))
    ex.lock_active_flow = True
    _wire_locked_loop(monkeypatch, ex, locked, target, spec)

    monkeypatch.setattr(
        continuation, "note_soft_abandon_strike", AsyncMock(return_value=3)
    )
    apply_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(continuation, "apply_soft_abandon", apply_spy)

    captured: list = []
    # First use_skill hits the gate (streak 3 -> abandon + unlock). The loop
    # re-runs the model; the second use_skill must dispatch on the unlocked
    # surface (no block observation).
    decisions = [
        {"action": "tool", "tool": "use_skill", "args": {"name": "faq_lookup"}},
        {"action": "tool", "tool": "use_skill", "args": {"name": "faq_lookup"}},
        {"action": "final", "answer": "ok"},
    ]

    async def _spy(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kwargs,
    ):
        idx = len(captured)
        captured.append([dict(o) for o in observations])
        return decisions[min(idx, len(decisions) - 1)]

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _spy)

    v = make_visitor(utterance="what are your hours")
    await ex.execute(v)

    apply_spy.assert_awaited_once()
    # After the abandon the surface is unlocked: the block framing must not be
    # re-emitted for the re-routed use_skill.
    blocks = [
        o
        for turn in captured
        for o in turn
        if o.get("tool") == "use_skill"
        and "cannot be started while" in (o.get("observation") or "")
    ]
    assert blocks == []
