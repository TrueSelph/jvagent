"""SkillExecutive configuration surface (ADR-0015): reasoning passthrough,
thinking/progress stream, budgets, and tooling/UX knobs (tier, block-raw,
transient ack, MCP tool-server selection)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.skill_executive.core_tools import build_core_tools
from jvagent.action.skill_executive.skill_executive_interact_action import (
    SkillExecutiveInteractAction,
)

pytestmark = pytest.mark.asyncio


# --- Phase 1: reasoning passthrough ---------------------------------------


async def test_reasoning_kwargs_disabled_by_default():
    assert SkillExecutiveInteractAction()._reasoning_kwargs() == {}


async def test_reasoning_kwargs_effort_and_budget():
    ex = SkillExecutiveInteractAction()
    ex.reasoning_effort = "high"
    ex.reasoning_budget_tokens = 2048
    ex.reasoning_extra = {"foo": "bar"}
    out = ex._reasoning_kwargs()
    assert out["reasoning_effort"] == "high"
    assert out["reasoning"]["effort"] == "high"
    assert out["reasoning"]["budget_tokens"] == 2048
    assert out["reasoning"]["foo"] == "bar"


async def test_reasoning_kwargs_explicit_disable():
    ex = SkillExecutiveInteractAction()
    ex.reasoning_enabled = False
    out = ex._reasoning_kwargs()
    assert out["reasoning_effort"] is None
    assert out["reasoning"] == {"enabled": False}


async def test_reasoning_threads_into_model_call(monkeypatch):
    ex = SkillExecutiveInteractAction()
    ex.reasoning_effort = "medium"
    ex.max_statement_length = 200
    captured = {}

    model = MagicMock()

    async def _qm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(response='{"action":"final","answer":"hi"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="Ex", role="a guide")

    monkeypatch.setattr(SkillExecutiveInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(SkillExecutiveInteractAction, "get_agent", _agent)

    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert captured.get("reasoning_effort") == "medium"
    assert "LENGTH LIMIT" in captured["system"]
    assert "200 characters" in captured["system"]


# --- Phase 2: thinking / progress stream ----------------------------------


async def test_progress_line_variants():
    pl = SkillExecutiveInteractAction._progress_line
    assert "research" in pl("tool", "use_skill", {"name": "research"}, {})
    assert pl("tool", "reply", {}, {}) == "Composing a reply…"
    assert pl("tool", "web_search", {}, {}) == "Using web_search…"
    assert pl("final", "", {}, {}) == "Wrapping up…"
    # An explicit model thought wins.
    assert pl("tool", "x", {}, {"thought": "Looking it up"}) == "Looking it up"


async def test_emit_thought_noop_without_bus():
    ex = SkillExecutiveInteractAction()
    v = MagicMock()
    v.response_bus = None
    v.session_id = None
    await ex._emit_thought(v, "thinking…")  # must not raise


async def test_emit_thought_publishes_thought_over_bus():
    ex = SkillExecutiveInteractAction()
    bus = MagicMock()
    bus.publish = AsyncMock()
    v = MagicMock()
    v.response_bus = bus
    v.session_id = "sess_1"
    v.channel = "web"
    v.interaction = SimpleNamespace(id="int_1", user_id="u")
    await ex._emit_thought(v, "thinking…")
    assert bus.publish.await_args.kwargs["category"] == "thought"
    assert bus.publish.await_args.kwargs["transient"] is True


async def test_reasoning_trace_emitted_when_enabled(monkeypatch):
    ex = SkillExecutiveInteractAction()
    ex.stream_reasoning_trace = True
    emitted = []

    async def _emit(self, visitor, text):
        emitted.append(text)

    monkeypatch.setattr(SkillExecutiveInteractAction, "_emit_thought", _emit)

    model = MagicMock()

    async def _qm(**kwargs):
        return SimpleNamespace(
            response='{"action":"final"}', thinking_content="step-by-step…"
        )

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(SkillExecutiveInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(SkillExecutiveInteractAction, "get_agent", _agent)
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert emitted == ["step-by-step…"]


# --- Phase 3: budgets ------------------------------------------------------


async def test_duration_guard_ends_turn(make_skill_executive, make_visitor):
    # A decision sequence that would loop forever; the wall-clock guard ends it.
    ex = make_skill_executive(
        decisions=[{"action": "tool", "tool": "noop", "args": {}}] * 50
    )
    ex.max_duration_seconds = 1e-9  # deadline already in the past → stop tick 1
    v = make_visitor()
    metrics = []
    v.interaction.observability_metrics = metrics
    v.interaction.save = AsyncMock()
    await ex.execute(v)
    ev = [m for m in metrics if m["event_type"] == "executive_activation"]
    assert ev and ev[-1]["data"]["ended_via"] == "duration"


# --- Phase 4: tooling / UX -------------------------------------------------


async def test_core_tools_tier_gating():
    ex = SkillExecutiveInteractAction()
    assert [t.name for t in build_core_tools(ex, "minimal")] == []
    assert "get_current_datetime" in [t.name for t in build_core_tools(ex, "standard")]
    assert "get_current_datetime" in [t.name for t in build_core_tools(ex, "full")]


async def test_block_raw_tool_invocation_gates_hidden(
    make_skill_executive, make_visitor
):
    ex = make_skill_executive(
        decisions=[
            {"action": "tool", "tool": "hidden_tool", "args": {}},
            {"action": "final", "answer": "done"},
        ]
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor()
    # hidden_tool is not in the visible surface → gated, loop continues to final.
    await ex.execute(v)
    assert v.interaction.response == "done"


async def test_select_mcp_actions_empty_without_servers():
    assert SkillExecutiveInteractAction()._select_mcp_actions([]) == []


async def test_select_mcp_actions_all_and_finite(monkeypatch):
    import sys
    import types

    class _FakeMCP:
        def get_class_name(self):
            return "MCPAction"

    fake = _FakeMCP()

    # The helper imports MCPAction lazily; inject a fake module so isinstance
    # matches our stub.

    fake_module = types.ModuleType("jvagent.action.mcp.mcp_action")
    fake_module.MCPAction = _FakeMCP
    monkeypatch.setitem(sys.modules, "jvagent.action.mcp.mcp_action", fake_module)

    ex = SkillExecutiveInteractAction()
    ex.tool_servers = "-all"
    assert ex._select_mcp_actions([fake]) == [fake]
    ex.tool_servers = ["MCPAction"]
    assert ex._select_mcp_actions([fake]) == [fake]
    ex.tool_servers = ["other"]
    assert ex._select_mcp_actions([fake]) == []
