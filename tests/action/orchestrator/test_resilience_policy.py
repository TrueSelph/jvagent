"""Orchestrator resilience policy (ADR-0046): fallback chain + breaker around
the model call, structured decisions on the JSON protocol, budget guard."""

from __future__ import annotations

import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from jvagent.action.model.contract import ModelCapabilities
from jvagent.action.model.language.base import ModelActionResult
from jvagent.action.model.resilience import MODEL_BREAKER, breaker_key
from jvagent.action.orchestrator.constants import BUDGET_EXHAUSTED, MODEL_ERROR_ACTION
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
    def __init__(self, name, caps=None, results=None, provider="fake", model="m"):
        self._name = name
        self._caps = caps or ModelCapabilities()
        self.results: List[Any] = list(results or [])
        self.calls: List[Dict[str, Any]] = []
        self.provider = provider
        self.model = model

    def get_class_name(self):
        return self._name

    def capabilities(self, model=None):
        return self._caps

    async def query_messages(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _reset_breaker():
    MODEL_BREAKER.reset()
    yield
    MODEL_BREAKER.reset()


def _bind(monkeypatch, ex, primary, registry=None):
    async def _gear(self, gear):
        return primary, primary.model, 0.2, 512, False

    async def _resolve(self, action_type, *, profile="heavy"):
        return (registry or {}).get(action_type)

    monkeypatch.setattr(OrchestratorInteractAction, "_gear_model", _gear)
    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_model_action", _resolve)
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _ORIGINAL_RUN_MODEL)


# --- fallback chain ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_failure_falls_back_within_the_tick(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    primary = _FakeModelAction("Primary", results=[RuntimeError("503")], model="p")
    backup = _FakeModelAction(
        "Backup",
        caps=ModelCapabilities(supports_parallel_tools=False, max_output_tokens=256),
        results=[ModelActionResult(response="from backup")],
        model="b",
    )
    ex.model_fallbacks = [{"model": "b", "model_action_type": "Backup"}]
    _bind(monkeypatch, ex, primary, {"Backup": backup})

    with bind_turn_cache():
        decision = await ex._run_model(
            make_visitor(), "hi", [], [_tool("reply"), _tool("x")], []
        )
        used = get_prompt_cache().get("fallbacks_used")

    assert decision["tool"] == "reply" and decision["args"]["text"] == "from backup"
    assert used == ["Backup:b"]
    call = backup.calls[0]
    assert call["model"] == "b"
    assert "parallel_tool_calls" not in call  # the fallback's own capability gate
    assert call["max_tokens"] == 256  # clamped to the fallback's ceiling
    assert MODEL_BREAKER.snapshot()[breaker_key(primary, "p")]["failures"] == 1


@pytest.mark.asyncio
async def test_all_candidates_failing_is_a_model_error(make_visitor, monkeypatch):
    ex = OrchestratorInteractAction()
    primary = _FakeModelAction("Primary", results=[RuntimeError("down")], model="p")
    backup = _FakeModelAction("Backup", results=[RuntimeError("also down")], model="b")
    ex.model_fallbacks = ["b-alt", {"model": "b", "model_action_type": "Backup"}]
    primary.results.append(RuntimeError("down again"))  # for the bare-string fallback
    _bind(monkeypatch, ex, primary, {"Backup": backup})

    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert decision["action"] == MODEL_ERROR_ACTION
    assert "also down" in decision["error"]
    assert len(primary.calls) == 2 and primary.calls[1]["model"] == "b-alt"


@pytest.mark.asyncio
async def test_open_circuit_is_skipped_and_reopens_on_probe_failure(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.circuit_breaker_failures = 1
    ex.circuit_breaker_cooldown_seconds = 3600
    primary = _FakeModelAction("Primary", results=[RuntimeError("down")], model="p")
    backup = _FakeModelAction(
        "Backup",
        results=[ModelActionResult(response="one"), ModelActionResult(response="two")],
        model="b",
    )
    ex.model_fallbacks = [{"model": "b", "model_action_type": "Backup"}]
    _bind(monkeypatch, ex, primary, {"Backup": backup})

    with bind_turn_cache():
        first = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert first["args"]["text"] == "one"
    assert MODEL_BREAKER.is_open(breaker_key(primary, "p"))

    # Second turn: the primary is not even attempted (circuit open).
    with bind_turn_cache():
        second = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert second["args"]["text"] == "two"
    assert len(primary.calls) == 1


@pytest.mark.asyncio
async def test_healthcheck_reports_circuits():
    ex = OrchestratorInteractAction()
    MODEL_BREAKER.threshold = 1
    MODEL_BREAKER.record_failure("X:m", "boom")
    health = await ex.healthcheck()
    assert health["enabled"] is True
    assert health["model_circuits"]["X:m"]["open"] is True


# --- structured decisions (JSON protocol) ------------------------------------------


@pytest.mark.asyncio
async def test_json_protocol_sends_the_decision_schema_when_supported(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.tool_protocol = "json"
    primary = _FakeModelAction(
        "OpenAI",
        caps=ModelCapabilities(supports_structured_output=True),
        results=[ModelActionResult(response='{"action":"final","answer":"ok"}')],
        provider="openai",
    )
    _bind(monkeypatch, ex, primary)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    fmt = primary.calls[0]["response_format"]
    assert (
        fmt["type"] == "json_schema"
        and fmt["json_schema"]["name"] == "orchestrator_decision"
    )
    assert fmt["json_schema"]["schema"]["properties"]["action"]["enum"] == [
        "tool",
        "final",
    ]
    assert decision == {"action": "final", "answer": "ok"}


@pytest.mark.asyncio
async def test_json_protocol_forces_a_decision_tool_on_anthropic(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.tool_protocol = "json"
    primary = _FakeModelAction(
        "Anthropic",
        caps=ModelCapabilities(supports_structured_output=True),
        results=[
            ModelActionResult(
                response="",
                tool_calls=[
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {
                            "name": "orchestrator_decision",
                            "arguments": '{"action":"tool","tool":"reply","args":{"text":"hey"}}',
                        },
                    }
                ],
                finish_reason="tool_use",
            )
        ],
        provider="anthropic",
    )
    _bind(monkeypatch, ex, primary)
    with bind_turn_cache():
        decision = await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    call = primary.calls[0]
    assert call["tools"][0]["function"]["name"] == "orchestrator_decision"
    assert call["tool_choice"]["function"]["name"] == "orchestrator_decision"
    assert "response_format" not in call
    assert decision == {"action": "tool", "tool": "reply", "args": {"text": "hey"}}


@pytest.mark.asyncio
async def test_json_protocol_falls_back_to_json_mode_when_unsupported(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.tool_protocol = "json"
    primary = _FakeModelAction(
        "Groq",
        caps=ModelCapabilities(),
        results=[ModelActionResult(response='{"action":"final"}')],
    )
    _bind(monkeypatch, ex, primary)
    with bind_turn_cache():
        await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert primary.calls[0]["response_format"] == {"type": "json_object"}
    ex.structured_decisions = False
    primary.results.append(ModelActionResult(response='{"action":"final"}'))
    primary._caps = ModelCapabilities(supports_structured_output=True)
    with bind_turn_cache():
        await ex._run_model(make_visitor(), "hi", [], [_tool("reply")], [])
    assert primary.calls[1]["response_format"] == {"type": "json_object"}


# --- budget guard -------------------------------------------------------------------


def _model_call_event(prompt=1_000_000, completion=0):
    return {
        "event_type": "model_call",
        "data": {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        },
        "timestamp": time.time(),
    }


def test_turn_cost_is_summed_from_model_call_events(make_visitor):
    ex = OrchestratorInteractAction()
    v = make_visitor()
    v.interaction.observability_metrics = [
        _model_call_event(),
        _model_call_event(),
        {"event_type": "other"},
    ]
    assert ex._turn_cost_usd(v) == pytest.approx(0.30)
    ex.max_turn_cost_usd = 0.25
    assert ex._turn_budget_exhausted(v) is True
    ex.max_turn_cost_usd = 0.0
    assert ex._turn_budget_exhausted(v) is False


def test_turn_cost_prefers_litellm_reported_cost_over_estimate(make_visitor):
    ex = OrchestratorInteractAction()
    visitor = make_visitor()
    event = _model_call_event(prompt=1_000_000)
    event["data"]["cost_usd"] = 0.0123
    event["data"]["cost_source"] = "litellm_response_cost"
    visitor.interaction.observability_metrics = [event]

    assert ex._turn_cost_usd(visitor) == pytest.approx(0.0123)


def test_unknown_provider_cost_fails_closed_for_turn_budget(make_visitor):
    ex = OrchestratorInteractAction()
    ex.max_turn_cost_usd = 1.0
    visitor = make_visitor()
    event = _model_call_event(prompt=100, completion=10)
    event["data"].update(provider="unlisted-provider", model="private-model")
    visitor.interaction.observability_metrics = [event]

    assert ex._turn_cost_usd(visitor) == 0.0
    assert ex._turn_budget_exhausted(visitor) is True


def test_reported_zero_cost_is_complete_not_unknown(make_visitor):
    ex = OrchestratorInteractAction()
    ex.max_turn_cost_usd = 1.0
    visitor = make_visitor()
    event = _model_call_event(prompt=100, completion=10)
    event["data"].update(cost_usd=0.0, cost_source="provider_reported")
    visitor.interaction.observability_metrics = [event]

    assert ex._turn_cost_usd(visitor) == 0.0
    assert ex._turn_budget_exhausted(visitor) is False


def test_failed_transport_attempt_without_cost_fails_closed(make_visitor):
    ex = OrchestratorInteractAction()
    ex.max_turn_cost_usd = 1.0
    visitor = make_visitor()
    visitor.interaction.observability_metrics = [
        {
            "event_type": "model_attempt",
            "data": {
                "operation": "language_model_query",
                "provider": "ollama",
                "model": "glm-5.3:cloud",
                "outcome": "failed",
                "usage_status": "provider_unreported",
            },
        }
    ]

    assert ex._turn_cost_usd(visitor) == 0.0
    assert ex._turn_budget_exhausted(visitor) is True


@pytest.mark.asyncio
async def test_turn_ceiling_ends_the_loop_with_one_partial_compose(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[ReplyAction()])
    ex.max_turn_cost_usd = 0.10
    seen: Dict[str, Any] = {}

    async def _record(self, visitor, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        OrchestratorInteractAction, "_record_orchestrator_activation", _record
    )
    calls = {"n": 0}

    async def _rm(self, visitor, utterance, history, tools, observations, *a, **k):
        calls["n"] += 1
        # each model call "costs" $0.15 — recorded the way the model layer does
        visitor.interaction.observability_metrics.append(_model_call_event())
        if k.get("finalize"):
            return {"action": "final", "answer": "here is what I have"}
        return {"action": "tool", "tool": "get_current_datetime", "args": {}}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    v = make_visitor(utterance="research everything")
    v.interaction.observability_metrics = []
    await ex.execute(v)

    assert seen["ended_via"] == f"{BUDGET_EXHAUSTED}_finalized"
    assert calls["n"] == 2  # one working tick, then the single finalize compose
    assert "here is what I have" in (v.interaction.response or "")


@pytest.mark.asyncio
async def test_conversation_ceiling_blocks_the_turn_without_a_model_call(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    ex = make_orchestrator(actions=[ReplyAction()])
    ex.max_conversation_cost_usd = 1.0
    calls = {"n": 0}

    async def _rm(self, *a, **k):
        calls["n"] += 1
        return {"action": "final", "answer": "should not run"}

    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    seen: Dict[str, Any] = {}

    async def _record(self, visitor, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        OrchestratorInteractAction, "_record_orchestrator_activation", _record
    )
    v = make_visitor(utterance="hello")
    v.conversation.context["_cost_usd_total"] = 1.5
    await ex.execute(v)

    assert calls["n"] == 0
    assert ex.budget_exhausted_text in (v.interaction.response or "")
    assert seen["ended_via"] == "conversation_budget"


@pytest.mark.asyncio
async def test_turn_cost_is_settled_onto_the_conversation_when_a_ceiling_is_set(
    make_visitor,
):
    ex = OrchestratorInteractAction()
    v = make_visitor()
    v.interaction.observability_metrics = [_model_call_event()]
    assert await ex._settle_conversation_cost(v) == pytest.approx(0.15)
    assert "_cost_usd_total" not in v.conversation.context  # no ceiling → no write
    ex.max_conversation_cost_usd = 5.0
    v.conversation.context["_cost_usd_total"] = 0.5
    await ex._settle_conversation_cost(v)
    assert v.conversation.context["_cost_usd_total"] == pytest.approx(0.65)
    v.conversation.save.assert_awaited()


@pytest.mark.asyncio
async def test_unknown_provider_marks_conversation_cost_incomplete_and_blocks_next_turn(
    make_visitor,
):
    ex = OrchestratorInteractAction()
    ex.max_conversation_cost_usd = 5.0
    visitor = make_visitor()
    event = _model_call_event(prompt=100, completion=10)
    event["data"].update(provider="unlisted-provider", model="private-model")
    visitor.interaction.observability_metrics = [event]

    await ex._settle_conversation_cost(visitor)

    assert visitor.conversation.context["_cost_accounting_incomplete"] is True
    assert visitor.conversation.context.get("_cost_usd_total", 0.0) == 0.0
    assert ex._conversation_budget_exhausted(visitor) is True


@pytest.mark.parametrize(
    "corrupt_total", [-0.01, "not-a-number", float("inf"), float("nan"), True]
)
def test_corrupt_conversation_cost_fails_closed(make_visitor, corrupt_total):
    ex = OrchestratorInteractAction()
    ex.max_conversation_cost_usd = 5.0
    visitor = make_visitor()
    visitor.conversation.context["_cost_usd_total"] = corrupt_total

    assert ex._conversation_budget_exhausted(visitor) is True


@pytest.mark.asyncio
async def test_cost_settlement_does_not_reset_corrupt_conversation_total(make_visitor):
    ex = OrchestratorInteractAction()
    ex.max_conversation_cost_usd = 5.0
    visitor = make_visitor()
    visitor.conversation.context["_cost_usd_total"] = -1.0
    visitor.interaction.observability_metrics = [_model_call_event()]

    with pytest.raises(RuntimeError, match="settlement failed"):
        await ex._settle_conversation_cost(visitor)

    assert visitor.conversation.context["_cost_usd_total"] == -1.0
    assert visitor.conversation.context["_cost_accounting_incomplete"] is True


@pytest.mark.asyncio
async def test_failed_cost_persistence_fails_closed_and_blocks_followup_spend(
    make_visitor, caplog
):
    ex = OrchestratorInteractAction()
    ex.max_conversation_cost_usd = 5.0
    visitor = make_visitor()
    visitor.interaction.observability_metrics = [_model_call_event()]
    visitor.conversation.save.side_effect = OSError("sensitive database detail")

    with pytest.raises(RuntimeError, match="settlement failed") as raised:
        await ex._settle_conversation_cost(visitor)

    assert "sensitive database detail" not in str(raised.value)
    assert visitor.conversation.context["_cost_accounting_incomplete"] is True
    assert ex._conversation_budget_exhausted(visitor) is True
    assert "OSError" in caplog.text
    assert "sensitive database detail" not in caplog.text


@pytest.mark.asyncio
async def test_pilot_failure_still_settles_known_conversation_cost(
    make_visitor, monkeypatch
):
    ex = OrchestratorInteractAction()
    ex.skill_runtime = "capability_pilot"
    ex.max_conversation_cost_usd = 1.0
    visitor = make_visitor()
    visitor.interaction.observability_metrics = []
    visitor.conversation.id = "conversation-pilot-cost"

    async def run_failed_pilot(turn_visitor):
        turn_visitor.interaction.observability_metrics.append(_model_call_event())
        raise RuntimeError("pilot failed after a priced model response")

    monkeypatch.setattr(ex, "_curate_walk_path", AsyncMock())
    monkeypatch.setattr(ex, "_run_capability_pilot", run_failed_pilot)

    with pytest.raises(RuntimeError, match="priced model response"):
        await ex._execute_turn(visitor)

    assert visitor.conversation.context["_cost_usd_total"] == pytest.approx(0.15)
    visitor.conversation.save.assert_awaited()
