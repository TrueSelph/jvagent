"""Skill invocation normalization (the in-browser bug): the model addresses a
skill as if it were a tool — {"action":"use_skill","tool":"research"},
{"action":"research"}, or {"tool":"research"} — and the loop must rewrite any of
these to use_skill(name=<skill>) so the skill actually activates instead of
dispatching a non-existent tool."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.skills import SkillDoc
from jvagent.action.orchestrator.tools import SkillTool

pytestmark = pytest.mark.asyncio

_normalize = OrchestratorInteractAction._normalize


def _tools():
    async def _noop(args):
        return ""

    return {
        n: SkillTool(n, "d", run=_noop)
        for n in ("use_skill", "find_skill", "web_search__search", "reply", "respond")
    }


_SKILLS = {"research", "web_lookup"}


async def test_use_skill_with_name_in_tool_field():
    # {"action":"use_skill","tool":"research"} — the exact in-browser misfire.
    out = _normalize(
        {"action": "use_skill", "tool": "research", "args": {"topic": "x"}},
        _tools(),
        _SKILLS,
    )
    assert out == ("tool", "use_skill", {"name": "research"})


async def test_skill_name_as_action():
    assert _normalize({"action": "research"}, _tools(), _SKILLS) == (
        "tool",
        "use_skill",
        {"name": "research"},
    )


async def test_skill_name_as_tool():
    assert _normalize({"tool": "web_lookup"}, _tools(), _SKILLS) == (
        "tool",
        "use_skill",
        {"name": "web_lookup"},
    )


async def test_use_skill_canonical_shape_preserved():
    out = _normalize(
        {"action": "tool", "tool": "use_skill", "args": {"name": "research"}},
        _tools(),
        _SKILLS,
    )
    assert out == ("tool", "use_skill", {"name": "research"})


async def test_plain_tool_call_unaffected():
    out = _normalize(
        {"action": "tool", "tool": "web_search__search", "args": {"query": "q"}},
        _tools(),
        _SKILLS,
    )
    assert out == ("tool", "web_search__search", {"query": "q"})


async def test_reply_and_final_unaffected():
    assert _normalize({"action": "reply", "answer": "hi"}, _tools(), _SKILLS) == (
        "tool",
        "reply",
        {"text": "hi"},
    )
    action, tool, _ = _normalize(
        {"action": "final", "answer": "done"}, _tools(), _SKILLS
    )
    assert action == "final"


async def test_loop_activates_skill_from_malformed_decision(
    make_orchestrator, make_visitor, monkeypatch
):
    """End-to-end: a malformed use_skill decision activates the research skill
    (skills_used populated) instead of looping on a non-existent tool."""
    ex = make_orchestrator(
        decisions=[
            {"action": "use_skill", "tool": "research", "args": {"topic": "x"}},
            {"action": "final", "answer": ""},
        ]
    )

    def _docs(self, _agent):
        return [
            SkillDoc(
                name="research",
                description="Investigate a topic.",
                body="SOP: gather evidence, then answer.",
                requires_tools=(),
            )
        ]

    monkeypatch.setattr(OrchestratorInteractAction, "_discover_skills", _docs)

    v = make_visitor(utterance="use your research skill on solid-state batteries")
    v.interaction.observability_metrics = []
    v.interaction.save = AsyncMock()
    await ex.execute(v)

    ev = next(
        e
        for e in v.interaction.observability_metrics
        if e.get("event_type") == "orchestrator_activation"
    )
    assert "use_skill" in ev["data"]["tools_invoked"]  # routed to the meta-tool
    assert ev["data"]["skills_used"] == ["research"]  # skill actually activated
    assert "research" not in ev["data"]["tools_invoked"]  # never tried as a tool


async def test_loop_repeat_guard_breaks_on_self_repeat(
    make_orchestrator, make_visitor, monkeypatch
):
    """A model that keeps calling the same tool with the same args is broken out
    of by the repeat guard well before the activation budget is exhausted."""
    ex = make_orchestrator(
        decisions=[
            {"action": "tool", "tool": "use_skill", "args": {"name": "research"}}
        ]
        * 8
    )

    def _docs(self, _agent):
        return [SkillDoc(name="research", description="d", body="b", requires_tools=())]

    monkeypatch.setattr(OrchestratorInteractAction, "_discover_skills", _docs)

    v = make_visitor(utterance="x")
    v.interaction.observability_metrics = []
    v.interaction.save = AsyncMock()
    await ex.execute(v)

    ev = next(
        e
        for e in v.interaction.observability_metrics
        if e.get("event_type") == "orchestrator_activation"
    )
    assert ev["data"]["ended_via"] == "repeat_guard"
    assert ev["data"]["tick_count"] <= 5  # broke far below the budget (16)
    assert "(guard)" in ev["data"]["tools_invoked"]  # nudge was injected
