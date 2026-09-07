"""Issue #203 — a model must not be put on the degraded JSON contract by a
guess, and tool calls written as text must never reach the user.

- An inferred ``supports_tools=False`` (LiteLLM provider code, not its table)
  is unknown → native.
- ``auto`` → ``json`` is logged with the overrides; the activation event says
  why (``protocol_reason``).
- A provider that refuses native tools demotes the (action, model) pair to
  JSON for the process and the tick is redone on it.
- ``Tool Calls: [...]`` text under native is salvaged into a real call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import pytest

from jvagent.action.model.contract import ModelCapabilities
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import SkillTool, salvage_tool_call_text
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, get_prompt_cache

_ORIGINAL_RUN_MODEL = OrchestratorInteractAction._run_model


class _FakeModelAction:
    provider = "fake"

    def __init__(self, caps: ModelCapabilities, results: List[Any]):
        self._caps = caps
        self.results = list(results)
        self.calls: List[Dict[str, Any]] = []

    def get_class_name(self):
        return "FakeModelAction"

    def capabilities(self, model=None):
        return self._caps

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _bind(monkeypatch, ex, fake):
    async def _gear(self, gear):
        return fake, "ollama/glm-5.3:cloud", 0.2, 4096, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)


async def _noop(args):
    return "ok"


def _tool(name="a"):
    return SkillTool(
        name=name,
        description="does a thing",
        run=_noop,
        parameters_schema={"type": "object", "properties": {}},
    )


@pytest.fixture(autouse=True)
def _no_demotions():
    OrchestratorInteractAction._protocol_demotions.clear()
    yield
    OrchestratorInteractAction._protocol_demotions.clear()


# --- resolution --------------------------------------------------------------


def test_inferred_false_is_unknown_and_resolves_native():
    ex = OrchestratorInteractAction()
    with bind_turn_cache():
        assert (
            ex._resolve_protocol(ModelCapabilities(source="litellm(-tools)"))
            == "native"
        )
        assert get_prompt_cache()["protocol_reason"] == "auto:native"


def test_known_false_resolves_json_and_says_so(caplog):
    ex = OrchestratorInteractAction()
    with bind_turn_cache(), caplog.at_level(logging.WARNING):
        assert (
            ex._resolve_protocol(
                ModelCapabilities(supports_tools=False, source="bundled"),
                None,
                "ollama/gemma2:9b",
            )
            == "json"
        )
        assert get_prompt_cache()["protocol_reason"] == (
            "auto:json:supports_tools=False(bundled)"
        )
    assert any("tool_protocol auto → json" in r.getMessage() for r in caplog.records)
    assert any("tool_protocol: native" in r.getMessage() for r in caplog.records)


def test_configured_protocol_wins_and_is_recorded():
    ex = OrchestratorInteractAction()
    ex.tool_protocol = "json"
    with bind_turn_cache():
        assert ex._resolve_protocol(ModelCapabilities(supports_tools=True)) == "json"
        assert get_prompt_cache()["protocol_reason"] == "configured:json"


def test_tools_unsupported_errors_are_recognised():
    yes = (
        "400: registry.ollama.ai/library/x does not support tools",
        "This model does not support function calling",
        "Invalid parameter: 'tools' is not supported for this model",
        "unknown parameter: tool_choice",
    )
    no = ("rate limit exceeded", "context length exceeded", "connection reset", "")
    for text in yes:
        assert OrchestratorInteractAction._looks_like_tools_unsupported(text), text
    for text in no:
        assert not OrchestratorInteractAction._looks_like_tools_unsupported(text), text


# --- runtime demotion --------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_refusal_demotes_to_json_and_redoes_the_tick(
    make_visitor, monkeypatch, caplog
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(source="litellm(-tools)"),  # inferred: try native
        [
            RuntimeError("400 Bad Request: model does not support tools"),
            ModelActionResult(response='{"action": "final", "answer": "hello"}'),
            ModelActionResult(response='{"action": "final", "answer": "again"}'),
        ],
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor()
    with bind_turn_cache(), caplog.at_level(logging.WARNING):
        decision = await ex._run_model(v, "go", [], [_tool()], [])
        reason = get_prompt_cache()["protocol_reason"]
    assert decision == {"action": "final", "answer": "hello"}
    # First attempt carried native tools; the redo did not.
    assert fake.calls[0].get("tools")
    assert not fake.calls[1].get("tools")
    assert reason.startswith("demoted:json:")
    assert ("FakeModelAction", "ollama/glm-5.3:cloud") in (
        OrchestratorInteractAction._protocol_demotions
    )
    assert any("refused native tool calls" in r.getMessage() for r in caplog.records)
    # Next tick goes straight to JSON — no second probe.
    with bind_turn_cache():
        decision = await ex._run_model(v, "go", [], [_tool()], [])
    assert decision == {"action": "final", "answer": "again"}
    assert not fake.calls[2].get("tools")


@pytest.mark.asyncio
async def test_other_provider_errors_stay_model_errors(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=True),
        [RuntimeError("429 rate limit exceeded")],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "go", [], [_tool()], [])
    assert decision["action"] == "model_error"
    assert not OrchestratorInteractAction._protocol_demotions


# --- salvage -----------------------------------------------------------------

LEAK = """Tool Calls: [
{
"id": "call_8f2c1a91-3b4d-4e52-9f10-6a7d3f21e001",
"type": "function",
"name": "integral_get_track_schema",
"arguments": {
"track_id": "Identity"
}
}
]"""


def test_salvage_reads_the_exact_shape_from_the_report():
    calls = salvage_tool_call_text(LEAK)
    assert calls and len(calls) == 1
    assert calls[0]["function"]["name"] == "integral_get_track_schema"
    assert json.loads(calls[0]["function"]["arguments"]) == {"track_id": "Identity"}
    assert calls[0]["id"] == "call_8f2c1a91-3b4d-4e52-9f10-6a7d3f21e001"


def test_salvage_accepts_bare_arrays_and_openai_shape_and_mints_ids():
    calls = salvage_tool_call_text(
        '[{"function": {"name": "web_search", "arguments": "{\\"q\\": \\"x\\"}"}}]'
    )
    assert calls and calls[0]["function"]["name"] == "web_search"
    assert calls[0]["id"].startswith("salvaged_")
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "x"}


def test_salvage_leaves_prose_and_decisions_alone():
    for text in (
        "Here is what I found about tool calls: they are useful.",
        '{"action": "final", "answer": "done"}',
        '{"action": "tool", "tool": "web_search", "args": {}}',
        "[1, 2, 3]",
        "",
        "Tool Calls: not json at all",
    ):
        assert salvage_tool_call_text(text) is None, text


@pytest.mark.asyncio
async def test_tool_call_text_under_native_is_dispatched_not_replied(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=True),
        [ModelActionResult(response=LEAK, tool_calls=None)],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        decision = await ex._run_model(
            make_visitor(),
            "go",
            [],
            [_tool("integral_get_track_schema"), _tool("reply")],
            [],
        )
    assert decision["action"] == "tool"
    assert decision["tool"] == "integral_get_track_schema"
    assert decision["args"] == {"track_id": "Identity"}
