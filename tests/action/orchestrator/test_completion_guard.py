"""Plan-drain completion guard.

Regression: the model emitted a mid-task progress message ("Proceeding to the
report drafting step now") and the loop ENDED the turn, needing a user nudge.
The drain guard keeps a multi-step turn going while an active plan has open
steps — deflecting ``final`` and ``reply``/``respond`` (bare-narration-coerced
OR deliberate) until the model does the next step or closes the plan, bounded by
``plan_completion_max_deflections``. Inert when planning is off or no plan is open.
"""

from __future__ import annotations

import pytest

import jvagent.action.orchestrator.orchestrator_interact_action as oia
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.memory.task_store import TaskStore


class _FakePlan:
    def __init__(self, pending, checklist="1. [ ] write report"):
        self._pending = pending
        self._checklist = checklist

    def has_pending_steps(self):
        return self._pending

    def format_plan(self):
        return self._checklist


# --- _normalize: narration-coercion sentinel --------------------------------


def test_bare_text_coerced_to_reply():
    action, tool, args = OrchestratorInteractAction._normalize(
        {"answer": "I'll now synthesize the report next."},
        {"reply": object()},
    )
    assert (action, tool) == ("tool", "reply")
    assert args.get("text") == "I'll now synthesize the report next."


def test_deliberate_reply_passes_through():
    action, tool, args = OrchestratorInteractAction._normalize(
        {"action": "tool", "tool": "reply", "args": {"text": "Here you go."}},
        {"reply": object()},
    )
    assert (action, tool) == ("tool", "reply")
    assert args.get("text") == "Here you go."


def test_final_action_recognized():
    action, tool, _ = OrchestratorInteractAction._normalize(
        {"action": "final", "answer": "All done."},
        {"reply": object()},
    )
    assert action == "final"


def test_normalize_folds_flattened_tool_args():
    # Model put args at the decision top level (no "args" wrapper).
    action, tool, args = OrchestratorInteractAction._normalize(
        {"action": "tool", "tool": "update_plan", "steps": [{"step": "A"}]},
        {"update_plan": object()},
    )
    assert (action, tool) == ("tool", "update_plan")
    assert args.get("steps") == [{"step": "A"}]


def test_normalize_does_not_fold_when_args_present():
    # A well-formed args dict is trusted as-is — no stray top-level keys folded.
    _, _, args = OrchestratorInteractAction._normalize(
        {"action": "tool", "tool": "x", "args": {"a": 1}, "junk": [1, 2]},
        {"x": object()},
    )
    assert args == {"a": 1}


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


# --- plan-aware relevance pre-surfacing ------------------------------------


class _FakeTool:
    def __init__(self, description):
        self.description = description


def test_presurface_uses_plan_step_tokens():
    # A low-signal utterance surfaces nothing on its own, but the active plan's
    # checklist ("knowledge base") matches the tool description — so augmenting
    # the relevance signal with the plan text surfaces the right tool without a
    # find_tool round-trip.
    tools = {
        "pageindex__assimilate": _FakeTool(
            "Ingest one document into the knowledge base so it can be searched."
        )
    }
    cand = {"pageindex__assimilate"}

    assert (
        OrchestratorInteractAction._presurface_tools("Well?", cand, tools, 6) == set()
    )
    keep = OrchestratorInteractAction._presurface_tools(
        "Well?\n1. [ ] add the report to the knowledge base", cand, tools, 6
    )
    assert "pageindex__assimilate" in keep


# --- drain guard: live loop must not complete while plan has open steps -----


@pytest.mark.asyncio
async def test_plan_drain_deflects_reply_until_steps_closed(
    make_orchestrator, make_visitor, monkeypatch
):
    """A deliberate mid-plan reply ("Proceeding to drafting now") must be
    deflected while the plan has open steps; only after the model drains the
    plan (marks steps done) does a reply reach the user."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        visitor.interaction.response = (visitor.interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[
            # Stall: deliberate reply while a step is still open → deflected.
            {
                "action": "tool",
                "tool": "reply",
                "args": {"text": "Proceeding to the report drafting step now."},
            },
            # Model drains the plan (marks the step done).
            {
                "action": "tool",
                "tool": "update_plan",
                "args": {
                    "steps": [
                        {
                            "step": "Draft report",
                            "status": "done",
                            "result": "report.md",
                        }
                    ]
                },
            },
            # Now a reply reaches the user.
            {
                "action": "tool",
                "tool": "reply",
                "args": {"text": "Done — report saved."},
            },
            {"action": "final", "answer": ""},
        ],
    )
    ex.planning = True

    v = make_visitor(utterance="write the report")
    store = TaskStore(v.conversation)
    handle = await store.create(
        title="p",
        description="p",
        task_type="AGENTIC_LOOP",
        owner_action=ex.get_class_name(),
    )
    await handle.start()
    await handle.sync_plan([{"step": "Draft report", "status": "in_progress"}])

    await ex.execute(v)

    resp = v.interaction.response or ""
    # The mid-plan narration was deflected (never voiced); the post-drain reply was.
    assert "Proceeding to the report drafting" not in resp
    assert "Done — report saved." in resp


@pytest.mark.asyncio
async def test_no_plan_reply_completes_normally(
    make_orchestrator, make_visitor, monkeypatch
):
    """Without an active plan the drain guard is inert — a reply ends the turn."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        visitor.interaction.response = (visitor.interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[
            {"action": "tool", "tool": "reply", "args": {"text": "Here you go."}},
            {"action": "final", "answer": ""},
        ],
    )
    ex.planning = True  # planning on, but no plan exists → guard inert

    v = make_visitor(utterance="hi")
    await ex.execute(v)
    assert v.interaction.response == "Here you go."
