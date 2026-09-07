"""Reasoning continuity between ticks (ADR-0052, issue #203 defect 2).

The JSON contract replayed only ``TOOL …(…) → …`` lines, so a thinking model
re-derived its plan every tick and re-issued calls it had already made. Each
step now carries the model's own ``thought`` (or an excerpt of provider
reasoning) and both renderers replay it, bounded by ``thought_replay_max_chars``.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.action.model.contract import ModelCapabilities
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator.constants import DECISION_SCHEMA
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.prompts import render_protocol_section
from jvagent.action.orchestrator.tools import (
    SkillTool,
    render_observation_messages,
    render_observations_section,
)
from jvagent.action.orchestrator.turn_cache import bind_turn_cache
from jvagent.action.reply.reply_action import ReplyAction
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

_ORIGINAL_RUN_MODEL = OrchestratorInteractAction._run_model


# --- contract ----------------------------------------------------------------


def test_json_contract_asks_for_a_thought():
    assert "thought" in DECISION_SCHEMA["properties"]
    section = render_protocol_section("json")
    assert '"thought"' in section and "replayed to you" in section


# --- renderers ---------------------------------------------------------------


def _steps() -> List[Dict[str, Any]]:
    return [
        {
            "tool": "get_schema",
            "args": {"track": "Identity"},
            "observation": '{"type": "object", "key": "role"}',
            "assistant_text": "I have the schema; the type key is `role`. Next: write it.",
            "call_id": "c1",
        },
        {"tool": "(guard)", "guard": "repeat", "args": {}, "observation": "(nudge)"},
        {"tool": "write", "args": {"x": 1}, "observation": "ok"},
    ]


def test_json_renderer_replays_the_thought_before_the_result():
    out = render_observations_section(_steps())
    lines = out.splitlines()
    assert lines[0].startswith("THOUGHT: I have the schema")
    assert lines[1].startswith("TOOL get_schema(")
    # Steps without a thought render as before.
    assert "THOUGHT" not in "\n".join(lines[2:])


def test_thought_is_capped_and_can_be_switched_off():
    steps = _steps()
    steps[0]["assistant_text"] = "x" * 2000
    out = render_observations_section(steps, thought_max_chars=100)
    thought_line = out.splitlines()[0]
    assert thought_line.startswith("THOUGHT: ") and len(thought_line) < 200
    assert "THOUGHT" not in render_observations_section(steps, thought_max_chars=0)


def test_native_renderer_carries_the_thought_as_assistant_content():
    msgs = render_observation_messages(_steps())
    assistant = next(
        m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant["content"].startswith("I have the schema")
    capped = render_observation_messages(_steps(), thought_max_chars=20)
    assistant = next(
        m for m in capped if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert len(assistant["content"]) <= 24


# --- _run_model stamps the thought --------------------------------------------


class _FakeModelAction:
    provider = "fake"

    def __init__(self, caps, results):
        self._caps, self.results, self.calls = caps, list(results), []

    def get_class_name(self):
        return "FakeModelAction"

    def capabilities(self, model=None):
        return self._caps

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


def _bind(monkeypatch, ex, fake):
    async def _gear(self, gear):
        return fake, "fake-model", 0.2, 4096, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)


async def _noop(args):
    return "ok"


def _tool(name="a"):
    return SkillTool(
        name=name,
        description="d",
        run=_noop,
        parameters_schema={"type": "object", "properties": {}},
    )


@pytest.mark.asyncio
async def test_json_decision_thought_rides_as_assistant_text(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=False, source="bundled"),  # JSON contract
        [
            ModelActionResult(
                response='{"action":"tool","tool":"a","args":{},"thought":"Schema first."}'
            )
        ],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "go", [], [_tool()], [])
    assert decision["_assistant_text"] == "Schema first."


@pytest.mark.asyncio
async def test_provider_reasoning_is_the_fallback_on_both_protocols(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.thought_replay_max_chars = 40
    long_reasoning = "The user wants the schema, so I should fetch it " * 10
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=False, source="bundled"),
        [
            ModelActionResult(
                response='{"action":"tool","tool":"a","args":{}}',
                thinking_content=long_reasoning,
            )
        ],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "go", [], [_tool()], [])
    assert decision["_assistant_text"].startswith("The user wants")
    assert len(decision["_assistant_text"]) <= 44  # elided to the cap

    # Native: a tool call with no prose gets the excerpt too.
    fake2 = _FakeModelAction(
        ModelCapabilities(supports_tools=True),
        [
            ModelActionResult(
                response="",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "a", "arguments": "{}"},
                    }
                ],
                thinking_content=long_reasoning,
            )
        ],
    )
    _bind(monkeypatch, ex, fake2)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "go", [], [_tool()], [])
    assert decision["tool"] == "a" and decision["_assistant_text"].startswith(
        "The user wants"
    )

    # Off switch.
    ex.thought_replay_max_chars = 0
    fake3 = _FakeModelAction(
        ModelCapabilities(supports_tools=False, source="bundled"),
        [
            ModelActionResult(
                response='{"action":"tool","tool":"a","args":{}}', thinking_content="x"
            )
        ],
    )
    _bind(monkeypatch, ex, fake3)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "go", [], [_tool()], [])
    assert "_assistant_text" not in decision


# --- the loop carries it to the next tick ------------------------------------


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
            return ToolResult(content="SCHEMA{role}")

        return [Tool(name="tool_a", description="A", execute=a)]


@pytest.mark.asyncio
async def test_next_tick_sees_the_previous_thought_and_the_repeat_nudge_carries_the_result(
    make_orchestrator, make_visitor, monkeypatch
):
    tools = _Tools()
    ex = make_orchestrator(actions=[ReplyAction(), tools])
    captured: List[List[Dict[str, Any]]] = []
    seq = [
        {
            "action": "tool",
            "tool": "tool_a",
            "args": {},
            "_assistant_text": "Fetch the schema first.",
        },
        {"action": "tool", "tool": "tool_a", "args": {}},  # the repeat
        {"action": "final", "answer": "done"},
    ]

    async def _rm(self, visitor, utterance, history, visible, observations, *a, **k):
        captured.append([dict(o) for o in observations])
        if k.get("finalize"):
            return {"action": "final", "answer": "partial"}
        return seq.pop(0) if seq else {"action": "final", "answer": "done"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    await ex.execute(make_visitor(utterance="schema please"))
    # Tick 2's observations carry tick 1's thought, and the renderer replays it.
    step = next(o for o in captured[1] if o.get("tool") == "tool_a")
    assert step.get("assistant_text") == "Fetch the schema first."
    assert "THOUGHT: Fetch the schema first." in render_observations_section(
        captured[1]
    )
    # The repeat nudge (tick 3) quotes the earlier result instead of "above".
    nudge = next(o for o in captured[2] if o.get("tool") == "(guard)")
    assert "it returned: SCHEMA{role}" in nudge["observation"]
    assert tools.calls == ["a"]
