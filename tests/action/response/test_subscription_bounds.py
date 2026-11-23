"""A slow subscription cannot grow an unbounded queue or stall publishers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.response.streaming import stream_messages


@pytest.mark.asyncio
async def test_overflow_is_explicit_and_unsubscribes() -> None:
    async def subscribe(session_id, callback, **kwargs):
        for _ in range(257):
            await callback(SimpleNamespace(interaction_id="interaction"))

    bus = SimpleNamespace(
        subscribe=subscribe,
        get_messages=AsyncMock(return_value=[]),
        unsubscribe=AsyncMock(),
    )
    source = stream_messages("session", bus)
    assert "subscription_overflow" in await anext(source)
    with pytest.raises(StopAsyncIteration):
        await anext(source)
    bus.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_bus_wire_clock_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from jvagent.action.response.response_bus import App, ResponseBus

    monkeypatch.setattr(
        App,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                timezone="America/Guyana",
                now=AsyncMock(return_value=datetime(2026, 11, 5, 12)),
            )
        ),
    )
    assert await ResponseBus()._get_now() == datetime(
        2026, 11, 5, 16, tzinfo=timezone.utc
    )
