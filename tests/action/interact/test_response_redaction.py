"""Debug/observability redaction in build_interact_response.

Regression: the public endpoint must NOT strip the interaction payload (events,
observability_metrics, report) in local dev — that's the jvchat Debug view's
data. It redacts only in production, or when explicitly hardened via
``JVAGENT_INTERACT_REDACT_DEBUG`` on a non-prod internet deploy.
"""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.interact import response_builder as rb
from jvagent.action.interact.webhook_pipeline import build_interaction_log_data


def _interaction():
    return SimpleNamespace(
        id="int1",
        conversation_id="",  # falsy → skip Conversation lookup
        utterance="hi",
        response="hello",
        actions=[],
        directives=[],
        parameters=[],
        events=[{"type": "model_call"}],
        observability_metrics=[{"k": "v"}],
        usage={"total_tokens": 5},
        streamed=False,
    )


def test_export_observability_metrics_drops_harness_journal():
    kept = {"event_type": "model_call"}
    assert rb.export_observability_metrics(
        [
            kept,
            {"kind": "harness.turn_run", "payload": {}},
            {"kind": "harness.trace", "spans": []},
            "skip-non-dict-ok",
        ]
    ) == [kept, "skip-non-dict-ok"]


def test_interaction_log_strips_harness_journal_from_get_state():
    ix = _interaction()
    ix.observability_metrics = [
        {"event_type": "model_call"},
        {"kind": "harness.turn_run", "payload": {"fat": True}},
    ]
    ix.interpretation = None
    ix.anchors = []
    ix.started_at = None
    ix.completed_at = None
    ix.closed = True
    ix.channel = "web"
    ix.get_state = lambda: {
        "observability_metrics": ix.observability_metrics,
        "utterance": ix.utterance,
    }
    log_data, _ = build_interaction_log_data(ix, "app", "agent")
    assert log_data["interaction_data"]["observability_metrics"] == [
        {"event_type": "model_call"}
    ]
    assert ix.observability_metrics[1]["kind"] == "harness.turn_run"


async def test_dev_public_endpoint_keeps_full_debug(monkeypatch):
    monkeypatch.setattr(rb, "is_production_mode", lambda: False)
    monkeypatch.delenv("JVAGENT_INTERACT_REDACT_DEBUG", raising=False)
    out = await rb.build_interact_response(
        "u", "s", _interaction(), report=[{"step": 1}], public_endpoint=True
    )
    assert "interaction" in out
    assert out["interaction"]["observability_metrics"] == [{"k": "v"}]
    assert out["interaction"]["events"] == [{"type": "model_call"}]
    assert out["report"] == [{"step": 1}]


async def test_dev_hardened_flag_redacts_public_endpoint(monkeypatch):
    monkeypatch.setattr(rb, "is_production_mode", lambda: False)
    monkeypatch.setenv("JVAGENT_INTERACT_REDACT_DEBUG", "true")
    out = await rb.build_interact_response(
        "u", "s", _interaction(), report=[{"step": 1}], public_endpoint=True
    )
    assert "interaction" not in out and "report" not in out
    assert out["response"] == "hello"


async def test_production_always_redacts(monkeypatch):
    monkeypatch.setattr(rb, "is_production_mode", lambda: True)
    monkeypatch.delenv("JVAGENT_INTERACT_REDACT_DEBUG", raising=False)
    out = await rb.build_interact_response(
        "u", "s", _interaction(), report=[{"step": 1}], public_endpoint=True
    )
    assert "interaction" not in out and "report" not in out


async def test_dev_payload_strips_harness_journal_metrics(monkeypatch):
    monkeypatch.setattr(rb, "is_production_mode", lambda: False)
    monkeypatch.delenv("JVAGENT_INTERACT_REDACT_DEBUG", raising=False)
    ix = _interaction()
    ix.observability_metrics = [
        {"event_type": "model_call", "data": {"model": "x"}},
        {"kind": "harness.turn_run", "payload": {"runs": {}}},
        {"kind": "harness.trace", "spans": []},
    ]
    out = await rb.build_interact_response(
        "u", "s", ix, report=None, public_endpoint=False
    )
    assert out["interaction"]["observability_metrics"] == [
        {"event_type": "model_call", "data": {"model": "x"}},
    ]


async def test_non_public_caller_keeps_debug_in_dev(monkeypatch):
    monkeypatch.setattr(rb, "is_production_mode", lambda: False)
    monkeypatch.setenv("JVAGENT_INTERACT_REDACT_DEBUG", "true")
    # hardening only applies to the public endpoint; internal callers keep debug
    out = await rb.build_interact_response(
        "u", "s", _interaction(), report=[{"step": 1}], public_endpoint=False
    )
    assert "interaction" in out and "report" in out
