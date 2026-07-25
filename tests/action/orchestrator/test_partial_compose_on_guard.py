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
