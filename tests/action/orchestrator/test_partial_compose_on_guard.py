"""A turn that stalls must still deliver the work it already did.

Live failure: a research → report → assimilate request activated the skill,
recorded a plan and fetched a page, then looped on find_tool, tripped the
repeat-guard and replied "Sorry, I didn't quite catch that — could you
rephrase?", discarding all of it. The guard returned straight out of the loop,
skipping the partial-compose that exists for exactly this case.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def _finalizing_orchestrator(make_orchestrator, monkeypatch, decisions):
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    ex = make_orchestrator(actions=[reply], decisions=decisions)

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        interaction.response = (interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)
    return ex


async def test_repeat_guard_delivers_gathered_work(
    make_orchestrator, make_visitor, monkeypatch
):
    """Three identical calls trip the guard; the turn must compose an answer
    from the observations rather than fall through to clarify_text."""
    same = {"action": "tool", "tool": "find_tool", "args": {"query": "x"}}
    ex = _finalizing_orchestrator(
        make_orchestrator, monkeypatch, [same, same, same, same]
    )

    composed = {}

    async def _run_model(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kw,
    ):
        # The finalize pass is the only call with finalize=True.
        if kw.get("finalize"):
            composed["observations"] = len(observations)
            return {"action": "final", "answer": "Here is what I found so far."}
        return same

    monkeypatch.setattr(type(ex), "_run_model", _run_model)

    v = make_visitor(utterance="research and save it")
    await ex.execute(v)

    assert "Here is what I found so far." in (v.interaction.response or "")
    assert ex.clarify_text not in (v.interaction.response or "")
    assert composed.get("observations", 0) > 0


async def test_repeat_guard_salvages_when_finalize_emits_tool(
    make_orchestrator, make_visitor, monkeypatch
):
    """If finalize ignores STEP LIMIT and returns a tool call, still deliver
    salvage from observations — never clarify_text."""
    same = {"action": "tool", "tool": "find_tool", "args": {"query": "write file"}}
    plan_obs_decision = {
        "action": "tool",
        "tool": "update_plan",
        "args": {
            "steps": [
                {
                    "step": "Gather posts",
                    "status": "done",
                    "result": "Fetched 2026 posts on AI and professional work.",
                },
                {"step": "Assimilate", "status": "pending"},
            ]
        },
    }
    seq = [plan_obs_decision, same, same, same]

    ex = _finalizing_orchestrator(make_orchestrator, monkeypatch, seq)

    async def _run_model(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kw,
    ):
        if kw.get("finalize"):
            # Broken finalize: model keeps tool-calling.
            return {
                "action": "tool",
                "tool": "file_interface__describe_write_workspace",
                "args": {},
            }
        return seq.pop(0) if seq else same

    monkeypatch.setattr(type(ex), "_run_model", _run_model)
    v = make_visitor(utterance="report and assimilate")
    await ex.execute(v)
    body = v.interaction.response or ""
    assert ex.clarify_text not in body
    assert "Fetched 2026 posts" in body


async def test_guard_still_ends_the_turn(make_orchestrator, make_visitor, monkeypatch):
    """Breaking instead of returning must not let the loop keep running."""
    same = {"action": "tool", "tool": "find_tool", "args": {"query": "x"}}
    ex = _finalizing_orchestrator(make_orchestrator, monkeypatch, [same] * 20)
    ex.activation_budget = 20
    calls = {"n": 0}

    async def _run_model(
        self,
        visitor,
        utterance,
        history,
        tools,
        observations,
        flow_note="",
        skills_section="",
        **kw,
    ):
        if kw.get("finalize"):
            return {"action": "final", "answer": "done"}
        calls["n"] += 1
        return same

    monkeypatch.setattr(type(ex), "_run_model", _run_model)
    await ex.execute(make_visitor(utterance="go"))
    # Guard fires on the third identical call — not after the whole budget.
    assert calls["n"] <= 4, calls


def test_salvage_partial_answer_prefers_plan_results():
    from jvagent.action.orchestrator.loop_helpers import salvage_partial_answer

    obs = [
        {
            "tool": "update_plan",
            "args": {
                "steps": [
                    {
                        "step": "Gather",
                        "status": "done",
                        "result": "Fetched 2026 posts on AI.",
                    }
                ]
            },
            "observation": "(plan updated)",
        },
        {
            "tool": "find_tool",
            "args": {"query": "write file"},
            "observation": "(no match)",
        },
    ]
    out = salvage_partial_answer(obs)
    assert "Fetched 2026 posts" in out
    assert "continue" in out.lower()


def test_plan_drain_nudge_rejects_write_file_detour():
    from jvagent.action.orchestrator.orchestrator_interact_action import (
        OrchestratorInteractAction,
    )

    nudge = OrchestratorInteractAction._plan_drain_nudge("- Assimilate (pending)")
    text = nudge["observation"]
    assert "write file" in text  # explicit ban must name the anti-pattern
    assert "do NOT search for 'write file'" in text
    assert "pageindex__assimilate" in text or "assimilate" in text.lower()
