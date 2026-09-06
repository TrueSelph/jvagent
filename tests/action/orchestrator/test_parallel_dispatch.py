"""Parallel tool dispatch, opt-in (ADR-0048).

``max_concurrent_tools`` = 1 keeps one tool call per tick. Above 1, the sibling
calls a provider returned in the same response run together in one tick — each
still passing the pre-dispatch guards — and their results are recorded in
decision order, each with its own call id.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator import prompts as P
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import SkillTool
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, update_prompt_cache
from jvagent.action.reply.reply_action import ReplyAction
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

_ORIGINAL_RUN_MODEL = OrchestratorInteractAction._run_model


class _Tools:
    """Three cheap tools: ``a`` is slow, ``b`` is fast, ``c`` fails."""

    binds_tools_to_visitor = False
    enabled = True

    def __init__(self):
        self.calls: List[str] = []
        self.marks: List[str] = []

    def get_class_name(self):
        return "ToolsAction"

    async def get_tools(self):
        async def a(**kwargs):
            self.calls.append("a")
            self.marks.append("a_start")
            await asyncio.sleep(0.05)
            self.marks.append("a_end")
            return ToolResult(content="A result")

        async def b(**kwargs):
            self.calls.append("b")
            self.marks.append("b_start")
            self.marks.append("b_end")
            return ToolResult(content="B result")

        async def c(**kwargs):
            self.calls.append("c")
            raise RuntimeError("boom")

        return [
            Tool(name="tool_a", description="A", execute=a),
            Tool(name="tool_b", description="B", execute=b),
            Tool(name="tool_c", description="C", execute=c),
        ]


def _lead(name: str, call_id: str = "c1", args: Dict[str, Any] = None):
    return {
        "action": "tool",
        "tool": name,
        "args": dict(args or {}),
        "_call_id": call_id,
        "_group_id": "c1",
    }


def _sibling(name: str, call_id: str, args: Dict[str, Any] = None):
    return {
        "action": "tool",
        "tool": name,
        "args": dict(args or {}),
        "_call_id": call_id,
        "_group_id": "c1",
    }


def _capture(monkeypatch) -> Dict[str, Any]:
    seen: Dict[str, Any] = {}

    async def _record(self, visitor, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        OrchestratorInteractAction, "_record_orchestrator_activation", _record
    )
    return seen


def _drive(make_orchestrator, monkeypatch, tools, *, width, first, siblings, then):
    """A scripted model: the first tick returns ``first`` with ``siblings``
    queued as pending decisions (what ``_run_model`` does for a multi-call
    provider response); later ticks pop from ``then``."""
    ex = make_orchestrator(actions=[ReplyAction(), tools])
    ex.max_concurrent_tools = width
    seq = list(then)
    captured: List[List[Dict[str, Any]]] = []
    state = {"first": True}

    async def _rm(self, visitor, utterance, history, visible, observations, *a, **k):
        captured.append([dict(o) for o in observations])
        if k.get("finalize"):
            return {"action": "final", "answer": "partial"}
        if state["first"]:
            state["first"] = False
            update_prompt_cache("pending_decisions", [dict(s) for s in siblings])
            return dict(first)
        return seq.pop(0) if seq else {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    return ex, captured


@pytest.mark.asyncio
async def test_default_width_drains_siblings_one_per_tick(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        width=1,
        first=_lead("tool_a"),
        siblings=[_sibling("tool_b", "c2")],
        then=[],
    )
    await ex.execute(make_visitor(utterance="two lookups"))

    assert tools.calls == ["a", "b"]
    # Sequential: a finished before b started.
    assert tools.marks.index("a_end") < tools.marks.index("b_start")
    assert seen["tick_count"] == 3  # a, b, final
    assert seen["parallel_batches"] == 0


@pytest.mark.asyncio
async def test_width_two_runs_the_siblings_together_in_one_tick(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        width=2,
        first=_lead("tool_a"),
        siblings=[_sibling("tool_b", "c2")],
        then=[],
    )
    await ex.execute(make_visitor(utterance="two lookups"))

    assert sorted(tools.calls) == ["a", "b"]
    # Concurrent: b started while a was still running.
    assert tools.marks.index("b_start") < tools.marks.index("a_end")
    assert seen["tick_count"] == 2  # one batch tick, then final
    assert seen["parallel_batches"] == 1
    # The next model call saw both results, in decision order, each with its
    # own call id — although b finished first.
    results = [o for o in captured[1] if o["tool"] in ("tool_a", "tool_b")]
    assert [o["tool"] for o in results] == ["tool_a", "tool_b"]
    assert [o.get("call_id") for o in results] == ["c1", "c2"]
    assert results[0]["group_id"] == results[1]["group_id"] == "c1"


@pytest.mark.asyncio
async def test_egress_sibling_is_not_batched(
    make_orchestrator, make_visitor, monkeypatch, publish_log
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        width=3,
        first=_lead("tool_a"),
        siblings=[_sibling("reply", "c2", {"text": "Here you go"})],
        then=[],
    )
    await ex.execute(make_visitor(utterance="look up then tell me"))

    assert tools.calls == ["a"]
    assert seen["parallel_batches"] == 0
    # The reply still ran — on its own tick, drained from the queue.
    assert seen["tick_count"] == 2
    assert any("Here you go" in p["content"] for p in publish_log)


@pytest.mark.asyncio
async def test_a_duplicate_sibling_is_nudged_not_run(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        width=2,
        first=_lead("tool_a"),
        siblings=[_sibling("tool_a", "c2")],  # same tool, same args
        then=[],
    )
    await ex.execute(make_visitor(utterance="twice"))

    assert tools.calls == ["a"]
    assert seen["parallel_batches"] == 0
    nudges = [o for o in captured[1] if o["tool"] == "(guard)"]
    assert nudges and "already called tool_a" in nudges[0]["observation"]
    # The refused sibling's note carries its call id so the transcript still
    # answers that call.
    assert nudges[0].get("call_id") == "c2"


@pytest.mark.asyncio
async def test_an_errored_sibling_marks_its_own_repeat_guard_entry(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        width=2,
        first=_lead("tool_a"),
        siblings=[_sibling("tool_c", "c2")],
        then=[{"action": "tool", "tool": "tool_c", "args": {}}],  # retry c
    )
    await ex.execute(make_visitor(utterance="retry"))

    # c failed in the batch (a succeeded), so the model's retry of c is the
    # one allowed re-dispatch — not a repeat-guard nudge.
    assert tools.calls == ["a", "c", "c"]
    nudges = [
        o
        for turn in captured
        for o in turn
        if o["tool"] == "(guard)" and "already called" in o["observation"]
    ]
    assert nudges == []


class _FakeModelAction:
    def __init__(self, results: List[Any]):
        self.results = list(results)
        self.calls: List[Dict[str, Any]] = []

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


async def _noop(args):
    return "ok"


@pytest.mark.asyncio
async def test_provider_is_only_asked_for_single_calls_at_width_one(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        [ModelActionResult(response="hi"), ModelActionResult(response="hi")]
    )

    async def _gear(self, gear):
        return fake, "fake-model", 0.2, 256, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)
    tool = SkillTool(
        name="a",
        description="d",
        run=_noop,
        parameters_schema={"type": "object", "properties": {}},
    )
    v = make_visitor()
    with bind_turn_cache():
        await ex._run_model(v, "go", [], [tool], [])
    assert fake.calls[0].get("parallel_tool_calls") is False

    ex.max_concurrent_tools = 2
    with bind_turn_cache():
        await ex._run_model(v, "go", [], [tool], [])
    assert "parallel_tool_calls" not in fake.calls[1]


def test_prompt_rule_and_width_clamp():
    one = P.render_protocol_section("native")
    many = P.render_protocol_section("native", parallel_width=3)
    assert "One call per step" in one and "up to" not in one
    assert "up to 3 tool calls in one step" in many and "One call per step" not in many
    # The JSON contract is one decision per reply regardless of width.
    assert P.render_protocol_section("json", parallel_width=3) == (
        P.render_protocol_section("json")
    )

    ex = OrchestratorInteractAction()
    assert ex._max_concurrent_tools() == 1
    ex.max_concurrent_tools = 0
    assert ex._max_concurrent_tools() == 1
    ex.max_concurrent_tools = 3
    assert ex._max_concurrent_tools() == 3
