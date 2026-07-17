"""Observability must source from the per-request result, not the shared
instance attribute (AUDIT-actions HIGH, H15).

track_usage emission is deferred on the streaming path; by then a concurrent
request on the same shared model action instance can have overwritten
self._last_result, leaking one user's prompts/response into another's interaction
log. _emit_observability must use the explicitly-passed result."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.model.base import BaseModelAction

pytestmark = pytest.mark.asyncio


class _Stub(BaseModelAction):
    pass


def _result(tag):
    return SimpleNamespace(
        system=f"{tag}_system",
        prompt=f"{tag}_prompt",
        history=[{"role": "user", "content": f"{tag}_history"}],
        response=f"{tag}_response",
        provider=f"{tag}_provider",
        model=f"{tag}_model",
        metrics={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        is_streaming=False,
        calling_action_name=f"{tag}_action",
        finish_reason="stop",
        tool_calls=None,
        _usage_estimated=False,
    )


def _interaction():
    i = MagicMock()
    i.observability_metrics = []
    i.save = AsyncMock()
    return i


async def test_passed_result_wins_over_shared_last_result():
    action = _Stub()
    # Simulate a concurrent request having overwritten the shared attribute.
    action._last_result = _result("VICTIM")

    interaction = _interaction()
    mine = _result("MINE")

    await action._emit_observability(interaction, {"total_tokens": 2}, 0.1, result=mine)

    assert len(interaction.observability_metrics) == 1
    data = interaction.observability_metrics[0]["data"]
    # My request's data — never the concurrent VICTIM's.
    assert data["system_prompt"] == "MINE_system"
    assert data["user_prompt"] == "MINE_prompt"
    assert data["response"] == "MINE_response"
    assert data["provider"] == "MINE_provider"
    assert "VICTIM" not in str(data)


async def test_falls_back_to_last_result_when_none_passed():
    action = _Stub()
    action._last_result = _result("FALLBACK")
    interaction = _interaction()

    await action._emit_observability(interaction, {"total_tokens": 2}, 0.1)

    data = interaction.observability_metrics[0]["data"]
    assert data["system_prompt"] == "FALLBACK_system"
