"""Execution ownership is explicit graph configuration, never a runtime fallback."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.response.response_bus import ResponseBus
from jvagent.action.response.streaming import stream_messages
from jvagent.core.execution_owner import (
    require_native_local_ownership,
    uses_native_harness,
)


@pytest.mark.asyncio
async def test_graph_owner_selection_rejects_mixed_and_failed_lookups() -> None:
    native = NativeHarnessAction()
    legacy = SimpleNamespace(
        get_manifest=lambda: SimpleNamespace(pattern_orchestrator=True)
    )
    host = SimpleNamespace(get_actions=AsyncMock(return_value=[native]))
    assert await uses_native_harness(host) is True
    for actions in ([legacy], []):
        host.get_actions.return_value = actions
        assert await uses_native_harness(host) is False
    for actions in ([native, legacy], [native, NativeHarnessAction()]):
        host.get_actions.return_value = actions
        with pytest.raises(RuntimeError, match="only one"):
            await uses_native_harness(host)
    host.get_actions.side_effect = RuntimeError("graph unavailable")
    with pytest.raises(RuntimeError, match="graph unavailable"):
        await uses_native_harness(host)


@pytest.mark.asyncio
async def test_action_graph_failure_cannot_look_like_a_legacy_configuration(
    monkeypatch: Any,
) -> None:
    from jvagent.action.actions import Actions

    monkeypatch.setattr(
        Actions, "nodes", AsyncMock(side_effect=OSError("graph offline"))
    )
    manager = Actions()
    host = SimpleNamespace(get_actions=manager.get_actions)
    with pytest.raises(OSError, match="graph offline"):
        await uses_native_harness(host)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JVAGENT_CONVERSATION_LOCK_REDIS_URL", "redis://example.invalid"),
        ("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", "test-table"),
        ("JVAGENT_REQUIRE_DISTRIBUTED_CONVERSATION_LOCK", "true"),
        ("WEB_CONCURRENCY", "2"),
        ("UVICORN_WORKERS", "invalid"),
        ("WEB_CONCURRENCY", "0"),
    ],
)
def test_unqualified_declared_topology_is_rejected(
    monkeypatch: Any, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="Native"):
        require_native_local_ownership()


def test_host_context_compatibility_alias_is_same_service() -> None:
    from jvagent.action.orchestrator import host_context as old
    from jvagent.core import host_context as current

    assert old is current


@pytest.mark.asyncio
async def test_native_cursor_is_visibly_unsupported_without_legacy_replay() -> None:
    bus = ResponseBus()
    bus.native_execution = True
    frames = [frame async for frame in stream_messages("session", bus, cursor="old")]
    assert len(frames) == 1
    assert "native_cursor_replay_unsupported" in frames[0]
    assert not bus._subscribers


@pytest.mark.asyncio
async def test_native_embed_cancellation_does_not_open_legacy_recovery(
    monkeypatch: Any,
) -> None:
    import asyncio
    from importlib import import_module

    module = import_module("jvagent.embed.interact")
    recovery = AsyncMock()
    monkeypatch.setattr(module, "_mark_embed_recovery", recovery)
    task = asyncio.create_task(asyncio.sleep(30))
    await module._register_interact_task("native-session", task, native=True)
    await module._register_interact_task("native-thread", task, native=True)
    try:
        assert module.cancel_interact(session_id="native-session")
        with pytest.raises(asyncio.CancelledError):
            await task
        recovery.assert_not_called()
    finally:
        await module._clear_interact_task("native-session", task)
        await module._clear_interact_task("native-thread", task)
    assert task not in module._native_interact_tasks
