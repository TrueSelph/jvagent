"""M5 — Skills center think-act-observe tests (ADR-0010 §2.1).

The real loop runs; only the raw skill-model call is mocked. Exercises direct
answers, tool dispatch + observation accumulation, unknown tools, the iteration
cap, and the error path. No real LM.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.centers.skills_center import SkillsCenter, SkillTool
from jvagent.action.executive.contracts import ACTIVATE

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


def _mock_skill_model(monkeypatch, decisions):
    """Patch SkillsCenter._call_skill_model to pop canned decisions (acquires budget)."""
    seq = list(decisions)

    async def _call(self, ctx, task, tools, observations):
        ctx.use_model()
        return seq.pop(0) if seq else None

    monkeypatch.setattr(SkillsCenter, "_call_skill_model", _call)


def _mock_skill_model_constant(monkeypatch, decision):
    async def _call(self, ctx, task, tools, observations):
        ctx.use_model()
        return dict(decision)

    monkeypatch.setattr(SkillsCenter, "_call_skill_model", _call)


async def test_direct_final_answer(
    make_executive, make_visitor, publish_log, monkeypatch
):
    _mock_skill_model(monkeypatch, [{"action": "final", "answer": "the answer"}])
    skills = SkillsCenter()
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="solve it"))
    assert _contents(publish_log) == ["the answer"]


async def test_tool_then_final(make_executive, make_visitor, publish_log, monkeypatch):
    ran = {}

    async def _calc(args):
        ran["args"] = args
        return "12"

    _mock_skill_model(
        monkeypatch,
        [
            {"action": "tool", "tool": "calc", "args": {"x": 5, "y": 7}},
            {"action": "final", "answer": "result is 12"},
        ],
    )
    skills = SkillsCenter()
    skills.set_tools([SkillTool(name="calc", description="adds", run=_calc)])
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="add 5 and 7"))
    assert _contents(publish_log) == ["result is 12"]
    assert ran["args"] == {"x": 5, "y": 7}


async def test_unknown_tool_then_final(
    make_executive, make_visitor, publish_log, monkeypatch
):
    _mock_skill_model(
        monkeypatch,
        [
            {"action": "tool", "tool": "ghost"},
            {"action": "final", "answer": "done anyway"},
        ],
    )
    skills = SkillsCenter()
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="x"))
    assert _contents(publish_log) == ["done anyway"]


async def test_iteration_cap(make_executive, make_visitor, publish_log, monkeypatch):
    # Model never finishes — always asks for a tool.
    _mock_skill_model_constant(monkeypatch, {"action": "tool", "tool": "loop"})
    skills = SkillsCenter()
    skills.max_iterations = 2
    skills.exhausted_text = "out of steps"
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
        activation_budget=12,
    )
    await ex.execute(make_visitor(utterance="loop forever"))
    assert _contents(publish_log) == ["out of steps"]


async def test_model_failure_returns_error(
    make_executive, make_visitor, publish_log, monkeypatch
):
    async def _call(self, ctx, task, tools, observations):
        ctx.use_model()
        return None

    monkeypatch.setattr(SkillsCenter, "_call_skill_model", _call)
    skills = SkillsCenter()
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="x"))
    assert _contents(publish_log) == ["I ran into an error working on that."]


async def test_integrate_returns_to_executive(
    make_executive, make_visitor, publish_log, monkeypatch
):
    # on_done="integrate" → result lands in working memory, Executive frames it.
    from jvagent.action.executive.contracts import RESPOND

    _mock_skill_model(monkeypatch, [{"action": "final", "answer": "raw fact"}])
    skills = SkillsCenter()
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[
            ACTIVATE("SkillsCenter", on_done="integrate"),
            RESPOND("framed: raw fact"),
        ],
    )
    await ex.execute(make_visitor(utterance="look it up"))
    assert _contents(publish_log) == ["framed: raw fact"]


async def test_tolerates_tool_name_in_action_field(
    make_executive, make_visitor, publish_log, monkeypatch
):
    """Model deviation: tool name in `action` (plus a `tool` field) still dispatches."""
    ran = {}

    async def _search(args):
        ran["query"] = (args or {}).get("query")
        return "found something"

    _mock_skill_model(
        monkeypatch,
        [
            {
                "action": "web_search__search",
                "tool": "web_search__search",
                "args": {"query": "Eldon Marks"},
            },
            {"action": "final", "answer": "here's what I found"},
        ],
    )
    skills = SkillsCenter()
    skills.set_tools(
        [SkillTool(name="web_search__search", description="search", run=_search)]
    )
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="who is Eldon Marks?"))
    assert _contents(publish_log) == ["here's what I found"]
    assert ran["query"] == "Eldon Marks"


async def test_tolerates_action_is_tool_name_without_tool_field(
    make_executive, make_visitor, publish_log, monkeypatch
):
    """Model deviation: `action` IS the tool name, no separate `tool` field."""
    ran = {}

    async def _search(args):
        ran["query"] = (args or {}).get("query")
        return "ok"

    _mock_skill_model(
        monkeypatch,
        [
            {"action": "web_search__search", "args": {"query": "y"}},
            {"action": "final", "answer": "done"},
        ],
    )
    skills = SkillsCenter()
    skills.set_tools(
        [SkillTool(name="web_search__search", description="search", run=_search)]
    )
    ex = make_executive(
        centers={"SkillsCenter": skills},
        executive_script=[ACTIVATE("SkillsCenter", on_done="voice")],
    )
    await ex.execute(make_visitor(utterance="lookup"))
    assert _contents(publish_log) == ["done"]
    assert ran["query"] == "y"


async def test_build_agent_tools_wraps_action_get_tools():
    """The adapter turns each action's get_tools() Tool into a runnable SkillTool."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    class _FakeTool:
        name = "web_search__search"
        description = "Search the public web."

        async def call(self, **kwargs):
            return SimpleNamespace(content="RESULT for " + str(kwargs.get("query", "")))

    class _ToolAction:
        async def get_tools(self):
            return [_FakeTool()]

    mgr = MagicMock()
    mgr.get_all_actions = AsyncMock(return_value=[_ToolAction()])
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=mgr)

    sc = SkillsCenter()
    tools = await sc._build_agent_tools(agent)
    assert "web_search__search" in tools
    out = await tools["web_search__search"].run({"query": "kittens"})
    assert out == "RESULT for kittens"


async def test_build_agent_tools_no_agent():
    sc = SkillsCenter()
    assert await sc._build_agent_tools(None) == {}


async def test_build_agent_tools_tolerates_failing_action():
    from unittest.mock import AsyncMock, MagicMock

    class _BadAction:
        async def get_tools(self):
            raise RuntimeError("boom")

    mgr = MagicMock()
    mgr.get_all_actions = AsyncMock(return_value=[_BadAction()])
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=mgr)

    sc = SkillsCenter()
    assert await sc._build_agent_tools(agent) == {}
