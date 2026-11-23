"""Native PageIndex callbacks bind credentials, provider selection and usage."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai_harness")
from jvspatial.api.exceptions import AuthorizationError, ValidationError
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from jvagent.action.native_harness import NativeHarnessAction
from jvagent.action.pageindex import endpoints
from jvagent.action.pageindex.native_callback import CHANNEL, run_native_callback
from jvagent.action.pageindex.webhook_auth import WEBHOOK_PERMISSION
from jvagent.action.pageindex_read import PageIndexReadAction
from jvagent.core import execution_owner
from jvagent.harness import native_model
from jvagent.memory.interaction import Interaction


@pytest.mark.asyncio
async def test_other_actions_webhook_key_is_refused_before_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = PageIndexReadAction(webhook_api_key_id="target")
    agent = SimpleNamespace(get_action_by_type=AsyncMock(side_effect=[None, profile]))
    monkeypatch.setattr(endpoints.Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        execution_owner, "native_harness_action", AsyncMock(return_value=object())
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            user={
                "user_id": "service",
                "api_key_id": "other",
                "permissions": [WEBHOOK_PERMISSION],
            }
        ),
        json=AsyncMock(),
    )
    with pytest.raises(AuthorizationError):
        await endpoints.pageindex_llm_webhook(request, "target-agent")
    request.json.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_image", [False, True])
@pytest.mark.parametrize("configured_model", ["", "openai:gpt-4.1-nano"])
async def test_native_callback_preserves_response_and_receipts(
    monkeypatch: pytest.MonkeyPatch, with_image: bool, configured_model: str
) -> None:
    action = await NativeHarnessAction.create(input_media_types=["image/png"])
    seen = []

    def respond(messages: Any, info: Any) -> ModelResponse:
        content = messages[-1].parts[-1].content
        seen.append(content)
        assert not info.function_tools
        if with_image:
            assert any(isinstance(part, BinaryContent) for part in content)
        return ModelResponse(
            parts=[TextPart("Result")],
            usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.001")),
        )

    effective_model = configured_model or action.model
    model_name = effective_model.split(":", 1)[1]

    def resolve(model: str) -> FunctionModel:
        assert model == effective_model
        return FunctionModel(respond, model_name=model_name)

    monkeypatch.setattr(native_model, "qualified_model", resolve)
    payload = {"prompt": "Summarize", "model": model_name}
    if with_image:
        payload.update(image_base64="YWJj", mime_type="image/png")
    with pytest.raises(ValidationError):
        await run_native_callback(
            action,
            agent_id="agent",
            actor="service",
            payload={**payload, "model": "other-model"},
            configured_model=configured_model,
        )
    result = await run_native_callback(
        action,
        agent_id="agent",
        actor="service",
        payload=payload,
        configured_model=configured_model,
    )
    assert result == {
        "text": "Result",
        "model": model_name,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    rows = await Interaction.find({"channel": CHANNEL})
    assert len(rows) == len(seen) == 1
    assert rows[0].user_id == "service" and rows[0].closed
    assert rows[0].usage["total_tokens"] == 15
