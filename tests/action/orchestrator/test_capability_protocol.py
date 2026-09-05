"""Capability-driven loop knobs (ADR-0045): ``tool_protocol: auto``,
parallel-call gating, output-ceiling clamp, context pre-flight."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from jvagent.action.model.contract import ModelCapabilities
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import SkillTool
from jvagent.action.orchestrator.turn_cache import bind_turn_cache, get_prompt_cache

_ORIGINAL_RUN_MODEL = OrchestratorInteractAction._run_model


async def _noop(args):
    return "ok"


def _tool(name):
    return SkillTool(name=name, description="d", run=_noop)


class _FakeModelAction:
    provider = "fake"

    def __init__(self, caps: ModelCapabilities, results: List[Any]):
        self._caps = caps
        self.results = list(results)
        self.calls: List[Dict[str, Any]] = []

    def capabilities(self, model=None):
        return self._caps

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


def _bind(monkeypatch, ex, fake, max_tokens=4096):
    async def _gear(self, gear):
        return fake, "fake-model", 0.2, max_tokens, False

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)


# --- protocol resolution --------------------------------------------------------


def test_default_is_auto_and_reads_native_before_resolution():
    ex = OrchestratorInteractAction()
    assert ex.tool_protocol == "auto"
    assert ex._protocol() == "native"


def test_resolve_protocol_picks_json_only_for_known_non_tool_models():
    ex = OrchestratorInteractAction()
    with bind_turn_cache():
        assert ex._resolve_protocol(ModelCapabilities(supports_tools=False)) == "json"
        assert ex._protocol() == "json"
    with bind_turn_cache():
        assert ex._resolve_protocol(ModelCapabilities()) == "native"  # unknown → native
        assert ex._protocol() == "native"
    ex.tool_protocol = "json"
    with bind_turn_cache():
        assert ex._resolve_protocol(ModelCapabilities(supports_tools=True)) == "json"


@pytest.mark.asyncio
async def test_auto_uses_json_contract_for_a_model_without_tool_calling(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=False),
        [
            ModelActionResult(
                response='{"action":"tool","tool":"reply","args":{"text":"hi"}}'
            )
        ],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    kwargs = fake.calls[0]
    assert decision["tool"] == "reply"
    assert kwargs["tools"] is None and kwargs["response_format"] == {
        "type": "json_object"
    }
    assert "Reply with a single JSON object" in kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_parallel_flag_is_withheld_when_the_provider_lacks_it(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=True, supports_parallel_tools=False),
        [ModelActionResult(response="hello")],
    )
    _bind(monkeypatch, ex, fake)
    with bind_turn_cache():
        await ex._run_model(make_visitor(), "hi", [], [_tool("reply"), _tool("x")], [])
    kwargs = fake.calls[0]
    assert kwargs["tool_choice"] == "auto" and "parallel_tool_calls" not in kwargs


@pytest.mark.asyncio
async def test_max_tokens_is_clamped_to_the_model_output_ceiling(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(max_output_tokens=1024), [ModelActionResult(response="hello")]
    )
    _bind(monkeypatch, ex, fake, max_tokens=8000)
    with bind_turn_cache():
        await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert fake.calls[0]["max_tokens"] == 1024


# --- context pre-flight ---------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_is_trimmed_to_fit_a_small_context_window(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(
        ModelCapabilities(context_window=1200), [ModelActionResult(response="ok")]
    )
    _bind(monkeypatch, ex, fake, max_tokens=100)
    history = []
    for i in range(6):
        history.append(
            {"role": "user", "content": f"old question {i} " + "lorem ipsum " * 60}
        )
        history.append(
            {
                "role": "assistant",
                "content": f"old answer {i} " + "dolor sit amet " * 60,
            }
        )
    observations = [
        {
            "tool": "web_fetch__fetch",
            "args": {"url": "u"},
            "observation": "page " * 800,
            "call_id": f"c{i}",
        }
        for i in range(6)
    ]
    with bind_turn_cache():
        await ex._run_model(
            make_visitor(), "summarise", history, [_tool("reply")], observations
        )
        trims = get_prompt_cache().get("context_trims")
    kwargs = fake.calls[0]
    assert trims and trims > 0
    # Oldest history went first; the current message and the system prompt stay.
    roles = [m["role"] for m in kwargs["messages"]]
    assert roles[0] == "system"
    assert len(kwargs["history"]) < len(history)
    assert any(
        "summarise" in m["content"] for m in kwargs["messages"] if m["role"] == "user"
    )
    # The tool replay shrank but did not vanish (floor of 2 observations).
    assert [m for m in kwargs["messages"] if m["role"] == "tool"]


@pytest.mark.asyncio
async def test_unknown_context_window_trims_nothing(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    fake = _FakeModelAction(ModelCapabilities(), [ModelActionResult(response="ok")])
    _bind(monkeypatch, ex, fake, max_tokens=100)
    history = [
        {"role": "user", "content": "x " * 5000},
        {"role": "assistant", "content": "y " * 5000},
    ]
    with bind_turn_cache():
        await ex._run_model(make_visitor(), "hi", history, [_tool("reply")], [])
        assert get_prompt_cache().get("context_trims") is None
    assert len(fake.calls[0]["history"]) == 2


@pytest.mark.asyncio
async def test_activation_event_records_protocol_and_trims(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[ReplyAction()])
    fake = _FakeModelAction(
        ModelCapabilities(supports_tools=False),
        [
            ModelActionResult(
                response='{"action":"tool","tool":"reply","args":{"text":"hi"}}'
            )
        ],
    )
    _bind(monkeypatch, ex, fake)
    v = make_visitor(utterance="hi")
    v.interaction.observability_metrics = []
    await ex.execute(v)
    events = [
        e
        for e in v.interaction.observability_metrics
        if isinstance(e, dict) and e.get("event_type") == "orchestrator_activation"
    ]
    assert events and events[-1]["data"]["tool_protocol"] == "json"
    assert json.dumps(events[-1]["data"])  # serialisable
