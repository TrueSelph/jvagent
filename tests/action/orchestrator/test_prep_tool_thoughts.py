"""Server-injected interview prep surfaces in the TOOL CALLS panel."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, **kwargs) -> None:
        self.published.append(kwargs)


def _visitor(bus: _FakeBus) -> SimpleNamespace:
    return SimpleNamespace(
        response_bus=bus,
        session_id="sess1",
        channel="default",
        interaction=SimpleNamespace(id="int1", user_id="u1"),
    )


async def test_emit_server_prep_tool_thoughts_pairs_call_and_result():
    bus = _FakeBus()
    ex = OrchestratorInteractAction()
    observations = [
        {
            "tool": "interview__message_evaluation",
            "args": {},
            "observation": '{"applicable":[{"field":"user_name"}]}',
        }
    ]
    await ex._emit_server_prep_tool_thoughts(_visitor(bus), observations)
    assert len(bus.published) == 2
    call, result = bus.published
    assert call["thought_type"] == "tool_call"
    assert result["thought_type"] == "tool_result"
    assert call["segment_id"] == result["segment_id"]
    assert call["metadata"]["tool_name"] == "interview__message_evaluation"
