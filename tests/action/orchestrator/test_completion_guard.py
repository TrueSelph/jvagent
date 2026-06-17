"""Plan-gated completion guard + narration-coercion sentinel.

Regression: the model emitted a bare progress message ("I'll now synthesize the
report next") with no tool call; the loop coerced it to a ``reply`` and ENDED the
turn mid-task. The guard keeps a multi-step turn going while an active plan has
open steps, and ``_normalize`` tags narration-coerced replies so a *deliberate*
reply is never blocked.
"""

from __future__ import annotations

import jvagent.action.orchestrator.orchestrator_interact_action as oia
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


class _FakePlan:
    def __init__(self, pending, checklist="1. [ ] write report"):
        self._pending = pending
        self._checklist = checklist

    def has_pending_steps(self):
        return self._pending

    def format_plan(self):
        return self._checklist


# --- _normalize: narration-coercion sentinel --------------------------------


def test_bare_text_coerced_reply_is_tagged():
    action, tool, args = OrchestratorInteractAction._normalize(
        {"answer": "I'll now synthesize the report next."},
        {"reply": object()},
    )
    assert (action, tool) == ("tool", "reply")
    assert args.get("_coerced_from_text") is True
    assert args.get("text") == "I'll now synthesize the report next."


def test_deliberate_reply_is_not_tagged():
    action, tool, args = OrchestratorInteractAction._normalize(
        {"action": "tool", "tool": "reply", "args": {"text": "Here you go."}},
        {"reply": object()},
    )
    assert (action, tool) == ("tool", "reply")
    assert "_coerced_from_text" not in args


def test_final_is_not_tagged():
    action, tool, _ = OrchestratorInteractAction._normalize(
        {"action": "final", "answer": "All done."},
        {"reply": object()},
    )
    assert action == "final"


# --- _open_plan_step: plan-gating ------------------------------------------


def _act(planning=True):
    act = OrchestratorInteractAction()
    object.__setattr__(act, "planning", planning)
    return act


def test_open_plan_step_returns_checklist_when_pending(monkeypatch):
    monkeypatch.setattr(
        oia, "active_plan", lambda *a, **k: _FakePlan(True, "1. [ ] write report")
    )
    assert _act()._open_plan_step(visitor=object()) == "1. [ ] write report"


def test_open_plan_step_none_when_no_pending_steps(monkeypatch):
    monkeypatch.setattr(oia, "active_plan", lambda *a, **k: _FakePlan(False))
    assert _act()._open_plan_step(visitor=object()) is None


def test_open_plan_step_none_when_planning_off(monkeypatch):
    # Planning disabled → guard is inert regardless of any plan.
    monkeypatch.setattr(oia, "active_plan", lambda *a, **k: _FakePlan(True))
    assert _act(planning=False)._open_plan_step(visitor=object()) is None


def test_open_plan_step_none_when_no_plan(monkeypatch):
    monkeypatch.setattr(oia, "active_plan", lambda *a, **k: None)
    assert _act()._open_plan_step(visitor=object()) is None
