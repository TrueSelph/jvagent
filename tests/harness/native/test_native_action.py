"""Native ingress and delivery boundary regressions."""

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
@pytest.mark.parametrize("delivered", [True, False])
async def test_native_receipt_precedes_delivery(
    monkeypatch: Any, delivered: bool
) -> None:
    from jvagent.action.native_harness import native_harness_action as module

    async def stream(messages: Any, info: Any) -> AsyncIterator[str]:
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
        return delivered

    facade = SimpleNamespace(
        get_agent=AsyncMock(
            return_value=SimpleNamespace(get_actions=AsyncMock(return_value=[]))
        ),
        get_responder=AsyncMock(return_value=SimpleNamespace(publish=publish)),
        model="test",
        skill_directory=".",
        instructions="",
        max_requests=2,
        max_tokens=2000,
        timeout_seconds=10,
    )
    visitor = SimpleNamespace(
        interaction=interaction,
        user_id="user",
        session_id="session",
        utterance="hello",
        channel="web",
    )
    if delivered:
        await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "delivered"
    else:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await NativeHarnessAction.execute(facade, visitor)
        assert saved[-1]["status"] == "pending_delivery"
