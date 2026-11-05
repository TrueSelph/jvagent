"""Fast replies emitted before SSE subscription still appear on the wire."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interact import endpoints
from jvagent.action.response.response_bus import ResponseBus


@pytest.mark.asyncio
async def test_fast_turn_replays_only_its_messages(monkeypatch: Any) -> None:
    bus = ResponseBus()
    await bus.publish(
        channel="web",
        session_id="session",
        content="Earlier turn",
        interaction_id="other",
    )
    await bus.publish(
        channel="web",
        session_id="other-session",
        content="Other caller",
        interaction_id="foreign",
    )
    interaction = SimpleNamespace(id="fast", close_interaction=AsyncMock())
    walker = SimpleNamespace(
        interaction=None,
        conversation=None,
        agent_id="agent",
        user_id="user",
        session_id="session",
        response_bus=bus,
        background_actions=[],
        get_report=AsyncMock(return_value=[]),
    )

    async def spawn(agent: Any) -> None:
        walker.interaction = interaction
        await bus.publish(
            channel="web",
            session_id="session",
            content="Configuration rejected",
            interaction_id="fast",
        )

    walker.spawn = spawn
    for name in (
        "_issue_session_token",
        "flush_deferred_entities",
        "_finalize_usage",
        "_emit_interaction_log",
    ):
        monkeypatch.setattr(endpoints, name, AsyncMock(return_value=None))
    monkeypatch.setattr(
        endpoints,
        "build_interact_response",
        AsyncMock(return_value={"response": "Configuration rejected"}),
    )
    monkeypatch.setattr(
        "jvagent.core.conversation_health.service.maybe_score_after_interaction",
        AsyncMock(),
    )
    chunks = []

    async def consume() -> None:
        async for raw in endpoints._stream_interaction(
            walker, SimpleNamespace(id="agent")
        ):
            chunks.append(json.loads(raw.removeprefix("data: ").strip()))

    await asyncio.wait_for(consume(), 2)
    messages = [
        chunk["message"]
        for chunk in chunks
        if chunk.get("type") == "message"
        and chunk["message"].get("message_type") == "adhoc"
    ]
    assert len(messages) == 1, chunks
    assert messages[0]["content"] == "Configuration rejected"
    assert messages[0]["interaction_id"] == "fast"
    assert not any(
        "Earlier turn" in json.dumps(c) or "Other caller" in json.dumps(c)
        for c in chunks
    )
