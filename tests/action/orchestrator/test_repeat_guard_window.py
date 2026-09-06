"""Repeat guard over a window (audit M7): oscillation is caught, not only
back-to-back repeats; one retry after an errored attempt is still allowed."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.reply.reply_action import ReplyAction
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult


class _Tools:
    """An action exposing two cheap tools, one of which can be made to fail."""

    binds_tools_to_visitor = False
    enabled = True

    def __init__(self):
        self.calls: List[str] = []
        self.fail_next = False

    def get_class_name(self):
        return "ToolsAction"

    async def get_tools(self):
        async def a(**kwargs):
            self.calls.append("a")
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("boom")
            return ToolResult(content="A result")

        async def b(**kwargs):
            self.calls.append("b")
            return ToolResult(content="B result")

        return [
            Tool(name="tool_a", description="A", execute=a),
            Tool(name="tool_b", description="B", execute=b),
        ]


def _call(name: str) -> Dict[str, Any]:
    return {"action": "tool", "tool": name, "args": {}}


def _capture(monkeypatch):
    seen: Dict[str, Any] = {}

    async def _record(self, visitor, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        OrchestratorInteractAction, "_record_orchestrator_activation", _record
    )
    return seen


def _drive(make_orchestrator, monkeypatch, tools, decisions):
    ex = make_orchestrator(actions=[ReplyAction(), tools])
    seq = list(decisions)
    captured: List[List[Dict[str, Any]]] = []

    async def _rm(self, visitor, utterance, history, visible, observations, *a, **k):
        captured.append([dict(o) for o in observations])
        if k.get("finalize"):
            return {"action": "final", "answer": "partial"}
        return seq.pop(0) if seq else {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    return ex, captured


@pytest.mark.asyncio
async def test_oscillation_is_nudged_then_stopped(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        [
            _call("tool_a"),
            _call("tool_b"),
            _call("tool_a"),
            _call("tool_b"),
            _call("tool_a"),
        ],
    )
    await ex.execute(make_visitor(utterance="loop"))

    # a, b ran; the second a is a repeat → nudged, not dispatched; the second b
    # likewise; the third a is the second repeat → the turn stops.
    assert tools.calls == ["a", "b"]
    nudges = [o for turn in captured for o in turn if o["tool"] == "(guard)"]
    assert any("already called tool_a" in o["observation"] for o in nudges)
    assert any("already called tool_b" in o["observation"] for o in nudges)
    assert seen["ended_via"].startswith("repeat_guard")


@pytest.mark.asyncio
async def test_back_to_back_repeat_behaves_as_before(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        [_call("tool_a"), _call("tool_a"), _call("tool_a")],
    )
    await ex.execute(make_visitor(utterance="loop"))
    assert tools.calls == ["a"]  # nudge on the 2nd, stop on the 3rd
    assert seen["ended_via"].startswith("repeat_guard")


@pytest.mark.asyncio
async def test_a_repeat_after_an_error_gets_one_retry(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    tools.fail_next = True
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        [
            _call("tool_a"),  # errors
            _call("tool_b"),  # something else in between
            _call("tool_a"),  # retry allowed (earlier attempt errored)
            {"action": "tool", "tool": "reply", "args": {"text": "ok"}},
        ],
    )
    v = make_visitor(utterance="retry")
    await ex.execute(v)
    assert tools.calls == ["a", "b", "a"]
    assert "ok" in (v.interaction.response or "")


@pytest.mark.asyncio
async def test_repeats_outside_the_window_are_not_counted(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    ex, captured = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        [_call("tool_a"), _call("tool_b"), _call("tool_b")]
        + [{"action": "tool", "tool": "tool_b", "args": {"n": i}} for i in range(3)]
        + [
            _call("tool_a"),
            {"action": "tool", "tool": "reply", "args": {"text": "fin"}},
        ],
    )
    ex.repeat_guard_window = 3
    ex.activation_budget = 24
    v = make_visitor(utterance="window")
    await ex.execute(v)
    # tool_a's first call fell out of a 3-call window, so its later call ran.
    assert tools.calls.count("a") == 2
    assert "fin" in (v.interaction.response or "")


def test_window_attribute_default_and_floor():
    ex = OrchestratorInteractAction()
    assert ex.repeat_guard_window == 8
