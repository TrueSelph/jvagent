"""Stream cancellation edge cases for embed.interact."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.embed.interact import (
    _clear_interact_task,
    _register_interact_task,
    cancel_interact,
)
from jvagent.harness.contracts import NativeCaller, TurnRunState
from jvagent.harness.runtime import reset_runtime


@pytest.mark.asyncio
async def test_invalid_host_context_has_same_embed_stream_error(
    monkeypatch: Any,
) -> None:
    from jvagent.action.native_harness import NativeHarnessAction
    from jvagent.embed import interact_stream

    async def spawn(agent: Any) -> None:
        return None

    walker = SimpleNamespace(
        interaction=None,
        conversation=None,
        user_id="user",
        session_id="session",
        _bootstrap_error="invalid_host_context",
        spawn=spawn,
        get_report=AsyncMock(return_value=[{"code": "invalid_host_context"}]),
    )
    monkeypatch.setattr(
        "jvagent.action.interact.interact_walker.InteractWalker",
        lambda **kwargs: walker,
    )
    monkeypatch.setattr(
        "jvagent.core.cache.get_cached_agent",
        AsyncMock(
            return_value=SimpleNamespace(
                get_actions=AsyncMock(return_value=[NativeHarnessAction()])
            )
        ),
    )

    async def consume() -> list[dict[str, Any]]:
        return [
            frame
            async for frame in interact_stream(agent_id="agent", utterance="hello")
        ]

    frames = await asyncio.wait_for(consume(), timeout=2)
    assert frames == [
        {
            "type": "error",
            "code": "invalid_host_context",
            "message": "Host context could not be verified.",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("admission_delay", [0.0, 5.3])
async def test_native_fast_embed_reply_replays_only_the_current_interaction(
    monkeypatch: Any,
    admission_delay: float,
) -> None:
    from jvagent.action.native_harness import NativeHarnessAction
    from jvagent.action.response.response_bus import ResponseBus
    from jvagent.embed import interact_stream

    bus = ResponseBus()
    bus.native_execution = True
    await bus.publish(
        channel="web",
        session_id="native-session",
        content="Earlier turn",
        interaction_id="earlier",
    )
    interaction = SimpleNamespace(id="native-fast", close_interaction=AsyncMock())
    walker = SimpleNamespace(
        interaction=None,
        conversation=None,
        user_id="user",
        session_id="native-session",
        response_bus=bus,
        background_actions=[],
        get_report=AsyncMock(return_value=[]),
    )

    async def spawn(agent: Any) -> None:
        await asyncio.sleep(admission_delay)
        walker.interaction = interaction
        await bus.publish(
            channel="web",
            session_id="native-session",
            content="Fast native response",
            interaction_id=interaction.id,
        )

    walker.spawn = spawn
    monkeypatch.setattr(
        "jvagent.action.interact.interact_walker.InteractWalker",
        lambda **kwargs: walker,
    )
    monkeypatch.setattr(
        "jvagent.core.cache.get_cached_agent",
        AsyncMock(
            return_value=SimpleNamespace(
                get_actions=AsyncMock(return_value=[NativeHarnessAction()])
            )
        ),
    )
    monkeypatch.setattr("jvspatial.flush_deferred_entities", AsyncMock())
    monkeypatch.setattr(
        "jvagent.action.interact.webhook_pipeline.finalize_usage", AsyncMock()
    )
    monkeypatch.setattr(
        "jvagent.action.interact.response_builder.build_interact_response",
        AsyncMock(return_value={"response": "Fast native response"}),
    )
    monkeypatch.setattr(
        "jvagent.core.conversation_health.service.maybe_score_after_interaction",
        AsyncMock(),
    )
    legacy = AsyncMock(side_effect=AssertionError("Native embed opened legacy runtime"))
    monkeypatch.setattr("jvagent.harness.runtime.get_runtime", legacy)
    frames = [
        frame
        async for frame in interact_stream(
            agent_id="agent",
            utterance="hello",
            user_id="user",
            session_id="native-session",
        )
    ]
    messages = [frame["message"] for frame in frames if frame["type"] == "message"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Fast native response"
    assert messages[0]["interaction_id"] == "native-fast"
    assert frames[-1]["type"] == "final"
    legacy.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_interact_cancels_registered_task() -> None:
    async def slow_walk() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(slow_walk())
    await _register_interact_task("sess-cancel-test", task)
    await asyncio.sleep(0)

    assert cancel_interact(session_id="sess-cancel-test") is True

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_interact_marks_turn_recovery() -> None:
    rt = reset_runtime()
    caller = NativeCaller("ag", "u1", "sess-recover")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.acquire_session_lease("sess-recover")
    rt.bind_lease("sess-recover", corr)

    async def slow_walk() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(slow_walk())
    await _register_interact_task("sess-recover", task)
    await asyncio.sleep(0)

    assert cancel_interact(session_id="sess-recover") is True
    journal = rt.get_run(corr)
    assert journal is not None
    assert journal.state is TurnRunState.RECOVERY_REQUIRED
    assert journal.reason == "embed_cancel"

    with pytest.raises(asyncio.CancelledError):
        await task
    reset_runtime()


@pytest.mark.asyncio
async def test_register_interact_task_cancels_prior_stream_for_same_key() -> None:
    first_done = asyncio.Event()

    async def first() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            first_done.set()

    async def second() -> None:
        await asyncio.sleep(0)

    t1 = asyncio.create_task(first())
    await _register_interact_task("thread-1", t1)
    t2 = asyncio.create_task(second())
    await _register_interact_task("thread-1", t2)

    with pytest.raises(asyncio.CancelledError):
        await t1

    await t2
    await _clear_interact_task("thread-1", t2)
