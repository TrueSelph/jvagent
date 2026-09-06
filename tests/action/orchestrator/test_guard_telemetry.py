"""Which guard fired is reported, not just that one did.

Live evaluation (2026-09-05, §2.5): a ``(guard)`` step in ``tools_invoked``
was emitted by four different sites, so the trace had to be read from debug
logs. Each guard observation now carries ``guard`` and the activation event
lists them in order.
"""

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
    binds_tools_to_visitor = False
    enabled = True

    def __init__(self):
        self.calls: List[str] = []

    def get_class_name(self):
        return "ToolsAction"

    async def get_tools(self):
        async def a(**kwargs):
            self.calls.append("a")
            return ToolResult(content="A result")

        return [Tool(name="tool_a", description="A", execute=a)]


def _capture(monkeypatch) -> Dict[str, Any]:
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

    async def _rm(self, visitor, utterance, history, visible, observations, *a, **k):
        if k.get("finalize"):
            return {"action": "final", "answer": "partial"}
        return seq.pop(0) if seq else {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    return ex


@pytest.mark.asyncio
async def test_repeat_guard_is_named_in_the_activation_event(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    call = {"action": "tool", "tool": "tool_a", "args": {}}
    ex = _drive(make_orchestrator, monkeypatch, tools, [call, dict(call)])
    await ex.execute(make_visitor(utterance="again"))
    assert tools.calls == ["a"]
    assert "(guard)" in seen["tools_invoked"]
    assert seen["guards"] == ["repeat"]


@pytest.mark.asyncio
async def test_grounding_guard_is_named_after_its_parameter(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    # An invented multi-word proper noun with no tool call → grounding guard.
    ex = _drive(
        make_orchestrator,
        monkeypatch,
        tools,
        [
            {
                "action": "tool",
                "tool": "reply",
                "args": {"text": "He taught at the University of Toronto."},
            }
        ],
    )
    await ex.execute(make_visitor(utterance="where did he teach?"))
    assert seen["guards"], seen["tools_invoked"]
    assert all(g and g != "unknown" for g in seen["guards"])
    assert seen["guards"][0].startswith("grounding")


@pytest.mark.asyncio
async def test_a_clean_turn_reports_no_guards(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    seen = _capture(monkeypatch)
    ex = _drive(
        make_orchestrator, monkeypatch, tools, [{"action": "final", "answer": "hi"}]
    )
    await ex.execute(make_visitor(utterance="hello"))
    assert seen["guards"] == []
