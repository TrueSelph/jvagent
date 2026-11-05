"""Native ingress and delivery boundary regressions."""

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from jvagent.action.native_harness import NativeHarnessAction


@pytest.mark.asyncio
@pytest.mark.parametrize("delivered", [True, False, None])
async def test_native_receipt_precedes_delivery(
    monkeypatch: Any, delivered: bool | None
) -> None:
    from jvagent.action.native_harness import native_harness_action as module

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
        from jvagent.tooling.context import get_dispatch_context, get_tool_visitor

        assert get_tool_visitor() is visitor
        assert get_dispatch_context().user_id == "user"
        yield "ok"

    runtime = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("ok")]),
            stream_function=stream,
        )
    )
    monkeypatch.setattr(module, "build_native_agent", lambda *args, **kwargs: runtime)
    saved = []
    interaction = SimpleNamespace(
        native_run={},
        native_steps={},
        observability_metrics=[],
        compute_usage=lambda: {},
        has_emitted=lambda: False,
        get_previous_interaction=AsyncMock(return_value=None),
    )

    async def save() -> None:
        saved.append(dict(interaction.native_run))

    interaction.save = save

    async def publish(text: str, **kwargs: Any) -> bool:
        assert kwargs["message_id"].startswith("o.ResponseMessage.")
        assert saved[-1]["status"] == "pending_delivery"
        assert saved[-1]["usage"]["requests"] == 1
        if delivered is None:
            raise asyncio.CancelledError()
        return delivered

    facade = SimpleNamespace(
        get_agent=AsyncMock(
            return_value=SimpleNamespace(get_actions=AsyncMock(return_value=[]))
        ),
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish)),
        max_run_cost_usd="2",
        max_conversation_cost_usd="20",
        model="test",
        skill_directory=".",
        delegates=[],
        instructions="",
        max_requests=2,
        max_tokens=2000,
        timeout_seconds=10,
    )
    visitor = SimpleNamespace(
        interaction=interaction,
        conversation=SimpleNamespace(native_spend={}),
        user_id="user",
        session_id="session",
        utterance="hello",
        channel="web",
    )
    if delivered is None:
        with pytest.raises(asyncio.CancelledError):
            await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "delivery_unknown"
        assert saved[-1]["output"] == "ok"
        assert saved[-1]["usage_complete"] is True
    elif delivered:
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "delivered"
    else:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "pending_delivery"
    from jvagent.tooling.context import get_dispatch_context, get_tool_visitor

    assert get_dispatch_context() is None
    assert get_tool_visitor() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_repeated_cancellation_preserves_caller_and_receipt(
    write_fails: bool,
) -> None:
    import asyncio

    from jvagent.action.native_harness.native_harness_action import (
        persist_cancelled_receipt,
    )

    entered = asyncio.Event()
    release = asyncio.Event()
    saved = []
    interaction = SimpleNamespace(
        native_run={"run_id": "cancelled-run", "status": "cancelled"}
    )

    async def save() -> None:
        entered.set()
        await release.wait()
        if write_fails:
            raise OSError("graph unavailable")
        saved.append(dict(interaction.native_run))

    interaction.save = save

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await persist_cancelled_receipt(interaction)
            raise

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    task.cancel()
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bool(saved) is not write_fails


@pytest.mark.asyncio
async def test_dispatch_context_is_task_local_and_legacy_compatible() -> None:
    from jvagent.tooling.context import bind_dispatch_context, get_dispatch_context
    from jvagent.tooling.tool_executor import get_tool_visitor

    async def identity(user: str) -> str:
        visitor = SimpleNamespace(user_id=user, session_id="session", channel="web")
        with bind_dispatch_context(visitor):
            await asyncio.sleep(0)
            assert get_tool_visitor() is visitor
            return get_dispatch_context().user_id

    assert await asyncio.gather(identity("first"), identity("second")) == [
        "first",
        "second",
    ]
    assert get_dispatch_context() is None
    assert get_tool_visitor() is None
