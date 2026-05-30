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


async def test_agentic_default_budget_and_tokens():
    ex = SkillExecutiveInteractAction()
    assert ex.activation_budget == 24  # room for multistep tool work
    assert ex.model_max_tokens == 2048  # headroom for thinking models


async def test_finalize_clause_added_to_prompt(monkeypatch):
    captured = {}
    model = MagicMock()

    async def _qm(**kwargs):
        captured["system"] = kwargs["system"]
        return SimpleNamespace(response='{"action":"final","answer":"x"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(SkillExecutiveInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(SkillExecutiveInteractAction, "get_agent", _agent)
    ex = SkillExecutiveInteractAction()
    await ex._run_model(MagicMock(), "hi", [], [], [], finalize=True)
    assert "STEP LIMIT REACHED" in captured["system"]


async def test_partial_compose_on_budget_exhaustion(make_skill_executive, make_visitor):
    """When the loop runs out of budget mid-task, force one compose so the user
    gets a partial answer instead of the generic clarify fallback."""
    ex = make_skill_executive(
        activation_budget=2,
        decisions=[
            {"action": "tool", "tool": "noop", "args": {}},
            {"action": "tool", "tool": "noop", "args": {}},
            # consumed by the forced finalize call after the budget is spent
            {"action": "final", "answer": "Here's what I gathered so far."},
        ],
    )
    v = make_visitor(utterance="do a big multistep research task")
    await ex.execute(v)
    assert v.interaction.response == "Here's what I gathered so far."


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


async def test_block_raw_tool_policy_in_prompt_only_when_enabled(monkeypatch):
    captured = {}

    model = MagicMock()

    async def _qm(**kwargs):
        captured["system"] = kwargs["system"]
        return SimpleNamespace(response='{"action":"final"}')

    model.query_messages = _qm

    async def _gma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="", role="")

    monkeypatch.setattr(SkillExecutiveInteractAction, "get_model_action", _gma)
    monkeypatch.setattr(SkillExecutiveInteractAction, "get_agent", _agent)

    ex = SkillExecutiveInteractAction()
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert "TOOL-USE POLICY" not in captured["system"]  # off by default

    ex.block_raw_tool_invocation = True
    await ex._run_model(MagicMock(), "hi", [], [], [])
    assert "TOOL-USE POLICY" in captured["system"]
    assert "yours to select" in captured["system"]


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


async def test_user_named_tools_detection():
    f = SkillExecutiveInteractAction._user_named_tools
    names = {"web_search__search", "mcp_filesystem__write_file", "reply", "do_thing"}
    assert "do_thing" in f("please run do_thing", names)  # full name
    assert "mcp_filesystem__write_file" in f("use write_file now", names)  # mcp suffix
    assert "reply" not in f("reply to me", names)  # egress exempt
    assert f("hello there", names) == frozenset()  # no mention


def _fake_capability_action(name, calls):
    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_result import ToolResult

    async def _run(**k):
        calls["n"] += 1
        return ToolResult(content="ran")

    class _FakeAction:
        def get_class_name(self):
            return "FakeAction"

        async def get_tools(self):
            return [
                Tool(
                    name=name,
                    description="Does a thing.",
                    parameters_schema={"type": "object", "properties": {}},
                    execute=_run,
                )
            ]

    return _FakeAction()


async def test_steering_guard_deflects_named_tool_once(
    make_skill_executive, make_visitor
):
    calls = {"n": 0}
    ex = make_skill_executive(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},
            {"action": "final", "answer": "handled"},
        ],
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor(utterance="please run do_thing for me")
    await ex.execute(v)
    assert calls["n"] == 0  # the user-named tool was deflected, never dispatched
    assert v.interaction.response == "handled"


async def test_steering_guard_allows_after_one_deflection(
    make_skill_executive, make_visitor
):
    calls = {"n": 0}
    ex = make_skill_executive(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},  # deflected
            {"action": "tool", "tool": "do_thing", "args": {}},  # now allowed
            {"action": "final", "answer": "ok"},
        ],
    )
    ex.block_raw_tool_invocation = True
    v = make_visitor(utterance="run do_thing")
    await ex.execute(v)
    assert calls["n"] == 1  # re-plan re-issued it → genuine choice, allowed once


async def test_steering_guard_off_when_flag_disabled(
    make_skill_executive, make_visitor
):
    calls = {"n": 0}
    ex = make_skill_executive(
        actions=[_fake_capability_action("do_thing", calls)],
        decisions=[
            {"action": "tool", "tool": "do_thing", "args": {}},
            {"action": "final", "answer": "ok"},
        ],
    )
    # block_raw_tool_invocation defaults False → no guard, tool dispatches.
    v = make_visitor(utterance="run do_thing")
    await ex.execute(v)
    assert calls["n"] == 1


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


async def test_mcp_filesystem_read_write_roundtrip(
    make_skill_executive, make_visitor, monkeypatch
):
    """End-to-end: an MCP filesystem gateway's write/read tools are surfaced to
    the executive, dispatched, and run with the per-user dispatch context bound
    (so real MCP servers route to the caller's sandbox)."""
    import sys
    import types

    from jvagent.tooling.tool import Tool
    from jvagent.tooling.tool_executor import get_dispatch_context
    from jvagent.tooling.tool_result import ToolResult

    class _MCPBase:
        pass

    fake_mod = types.ModuleType("jvagent.action.mcp.mcp_action")
    fake_mod.MCPAction = _MCPBase
    monkeypatch.setitem(sys.modules, "jvagent.action.mcp.mcp_action", fake_mod)

    store: dict = {}
    seen = {}

    class FakeFsMcp(_MCPBase):
        def get_class_name(self):
            return "FakeFsMcp"

        async def get_tools(self):
            # A real MCP tool forwards **kwargs to the server, so the executive
            # must NOT inject a visitor kwarg (it would be serialized and fail).
            async def _write(path="", content="", **k):
                seen["write_ctx"] = get_dispatch_context()
                seen["write_kwargs"] = dict(k)
                store[path] = content
                return ToolResult(content=f"wrote {path}")

            async def _read(path="", **k):
                seen["read"] = True
                store["read_kwargs"] = dict(k)
                return ToolResult(content=store.get(path, "(missing)"))

            schema = {"type": "object", "properties": {}}
            return [
                Tool(
                    name="mcp_filesystem__write_file",
                    description="Write a file in the sandbox.",
                    parameters_schema=schema,
                    execute=_write,
                ),
                Tool(
                    name="mcp_filesystem__read_file",
                    description="Read a file from the sandbox.",
                    parameters_schema=schema,
                    execute=_read,
                ),
            ]

    fake = FakeFsMcp()
    ex = make_skill_executive(
        actions=[fake],
        decisions=[
            {
                "action": "tool",
                "tool": "mcp_filesystem__write_file",
                "args": {"path": "notes.txt", "content": "hello sandbox"},
            },
            {
                "action": "tool",
                "tool": "mcp_filesystem__read_file",
                "args": {"path": "notes.txt"},
            },
            {"action": "final", "answer": "done"},
        ],
    )
    v = make_visitor(user_id="alice")
    await ex.execute(v)

    assert store["notes.txt"] == "hello sandbox"  # write routed through
    assert seen.get("read") is True  # read routed through
    # The visitor (which holds the non-serializable ResponseBus) must NOT be
    # forwarded to the MCP tool — only the model's own args.
    assert "visitor" not in seen.get("write_kwargs", {})
    # Per-user routing context was bound for the dispatch (real MCP uses it to
    # pick the caller's sandbox subprocess).
    ctx = seen.get("write_ctx")
    assert ctx is not None and ctx.user_id == "alice"
