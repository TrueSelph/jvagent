"""Tests for whatsapp_call jvagent_latency log line."""

import logging
from unittest.mock import MagicMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


@pytest.mark.asyncio
async def test_record_activation_logs_whatsapp_call_latency(caplog, monkeypatch):
    orch = OrchestratorInteractAction()
    orch.lock_active_flow = True
    monkeypatch.setattr(orch, "_gearing_on", lambda: False)

    interaction = MagicMock()
    interaction.observability_metrics = []
    interaction.save = MagicMock(return_value=None)

    visitor = MagicMock()
    visitor.interaction = interaction
    visitor.channel = "whatsapp_call"

    with caplog.at_level(logging.INFO):
        await orch._record_orchestrator_activation(
            visitor,
            continuation_mode="none",
            flow_owner=None,
            tools_invoked=["reply"],
            tick_count=2,
            ended_via="final",
            activated=[],
            loop_duration_ms=1234,
            tool_timings=[{"name": "reply", "duration_ms": 50}],
        )

    assert any(
        "jvagent_latency channel=whatsapp_call loop_ms=1234" in r.message
        for r in caplog.records
    )
    ev = interaction.observability_metrics[0]
    assert ev["data"]["loop_duration_ms"] == 1234
    assert ev["data"]["tools"] == [{"name": "reply", "duration_ms": 50}]
