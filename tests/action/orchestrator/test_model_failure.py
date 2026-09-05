"""Model-failure semantics (audit M2/M3/F2/F5).

A provider fault is not a model choice. The loop retries once, then ends the
turn with ``model_unavailable_text`` — never ``clarify_text`` ("could you
rephrase?"), never a finalize call into the same dead endpoint. Truncated output
is told apart from garbled output.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from jvagent.action.orchestrator.constants import (
    MODEL_ERROR_ACTION,
    MODEL_TRUNCATED_ACTION,
)
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


def _capture_activation(monkeypatch):
    seen: Dict[str, Any] = {}

    async def _record(self, visitor, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(
        OrchestratorInteractAction, "_record_orchestrator_activation", _record
    )
    return seen


@pytest.mark.asyncio
async def test_two_consecutive_model_errors_end_the_turn_with_unavailable_text(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    calls = {"n": 0}

    async def _rm(self, *a, **k):
        calls["n"] += 1
        return {"action": MODEL_ERROR_ACTION, "error": "503"}

    ex = make_orchestrator(actions=[ReplyAction()])
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    seen = _capture_activation(monkeypatch)
    v = make_visitor(utterance="hello")

    await ex.execute(v)

    assert calls["n"] == 2  # one retry, then stop — no finalize call
    assert ex.model_unavailable_text in (v.interaction.response or "")
    assert ex.clarify_text not in (v.interaction.response or "")
    assert seen["ended_via"] == "model_error"


@pytest.mark.asyncio
async def test_single_model_error_is_retried_and_the_turn_proceeds(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    decisions: List[Any] = [
        {"action": MODEL_ERROR_ACTION, "error": "timeout"},
        {"action": "tool", "tool": "reply", "args": {"text": "Recovered."}},
    ]

    async def _rm(self, *a, **k):
        return decisions.pop(0)

    ex = make_orchestrator(actions=[ReplyAction()])
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    seen = _capture_activation(monkeypatch)
    v = make_visitor(utterance="hello")

    await ex.execute(v)

    assert "Recovered." in (v.interaction.response or "")
    assert ex.model_unavailable_text not in (v.interaction.response or "")
    assert seen["ended_via"] == "reply"


@pytest.mark.asyncio
async def test_model_error_after_work_keeps_the_salvage(
    make_orchestrator, make_visitor, monkeypatch
):
    """Work done before the provider died is not thrown away."""
    from jvagent.action.reply.reply_action import ReplyAction

    decisions: List[Any] = [
        {"action": "tool", "tool": "get_current_datetime", "args": {}},
        {"action": MODEL_ERROR_ACTION, "error": "503"},
        {"action": MODEL_ERROR_ACTION, "error": "503"},
    ]

    async def _rm(self, *a, **k):
        return decisions.pop(0)

    ex = make_orchestrator(actions=[ReplyAction()])
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    v = make_visitor(utterance="what time is it")

    await ex.execute(v)

    body = v.interaction.response or ""
    assert ex.model_unavailable_text in body
    assert ex.clarify_text not in body


@pytest.mark.asyncio
async def test_truncated_output_is_nudged_distinctly_from_garbled_output(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    captured: List[List[Dict[str, Any]]] = []
    decisions: List[Any] = [
        {"action": MODEL_TRUNCATED_ACTION},
        None,
        {"action": "tool", "tool": "reply", "args": {"text": "ok"}},
    ]

    async def _rm(self, visitor, utterance, history, tools, observations, *a, **k):
        captured.append([dict(o) for o in observations])
        return decisions.pop(0)

    ex = make_orchestrator(actions=[ReplyAction()])
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    v = make_visitor(utterance="hello")

    await ex.execute(v)

    assert "ok" in (v.interaction.response or "")
    notes = [o for o in captured[-1] if o["tool"] in ("(truncated)", "(parse)")]
    assert [o["tool"] for o in notes] == ["(truncated)", "(parse)"]
    assert "cut off by the output length limit" in notes[0]["observation"]
    # Native protocol: the parse nudge speaks of tool calls, not JSON.
    assert "neither a tool call nor a reply" in notes[1]["observation"]


@pytest.mark.asyncio
async def test_three_unusable_outputs_end_the_turn(
    make_orchestrator, make_visitor, monkeypatch
):
    from jvagent.action.reply.reply_action import ReplyAction

    async def _rm(self, *a, **k):
        return {"action": MODEL_TRUNCATED_ACTION}

    ex = make_orchestrator(actions=[ReplyAction()])
    monkeypatch.setattr(OrchestratorInteractAction, "_run_model", _rm)
    seen = _capture_activation(monkeypatch)
    v = make_visitor(utterance="hello")

    await ex.execute(v)

    assert seen["ended_via"].startswith("no_decision")
    assert v.interaction.response or ""  # the fallback still answers


def test_json_protocol_keeps_the_json_nudge():
    ex = OrchestratorInteractAction()
    assert "tool call" in ex._no_decision_nudge()
    ex.tool_protocol = "json"
    assert "valid JSON object" in ex._no_decision_nudge()


def test_tool_call_timeout_is_bounded_by_default():
    """A hung tool must not hang the turn (and the conversation lock) forever."""
    ex = OrchestratorInteractAction()
    assert ex.tool_call_timeout > 0
