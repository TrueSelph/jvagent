"""Operator request schemas cannot accept caller-supplied audit identity."""

import pytest
from pydantic import ValidationError

from jvagent.action.native_harness.endpoints import UsageReserveRequest


@pytest.mark.parametrize(
    "change",
    [
        {"usd": "0"},
        {"usd": "-1"},
        {"usd": "NaN"},
        {"usd": True},
        {"digest": "stale"},
        {"actor": "admin"},
        {"reason": " "},
        {"evidence": " "},
    ],
)
def test_invalid_operator_reserve_request_is_rejected(
    change: dict[str, object],
) -> None:
    payload = {
        "digest": "sha256:" + "0" * 64,
        "usd": "0.5",
        "evidence": "policy allowance",
        "reason": "unknown billing",
        **change,
    }
    with pytest.raises(ValidationError):
        UsageReserveRequest.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, {}])
async def test_missing_request_is_a_validation_error(body: object) -> None:
    from fastapi import Request
    from jvspatial.api.exceptions import ValidationError as APIValidationError

    from jvagent.action.native_harness.endpoints import apply_usage_reserve

    with pytest.raises(APIValidationError):
        await apply_usage_reserve(
            Request({"type": "http"}), "action", "interaction", body
        )


@pytest.mark.asyncio
async def test_recovery_cannot_cross_the_action_agent_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from jvspatial.api.exceptions import ResourceNotFoundError

    from jvagent.action.native_harness import NativeHarnessAction, endpoints
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.interaction import Interaction

    action = NativeHarnessAction(agent_id="expected", enabled=True)
    conversation = Conversation(user_id="owner", session_id="session")
    interaction = Interaction(
        conversation_id=conversation.id, user_id="owner", session_id="session"
    )
    monkeypatch.setattr(NativeHarnessAction, "get", AsyncMock(return_value=action))
    monkeypatch.setattr(Interaction, "find", AsyncMock(return_value=[interaction]))
    monkeypatch.setattr(Conversation, "find", AsyncMock(return_value=[conversation]))
    monkeypatch.setattr(
        Conversation,
        "get_agent",
        AsyncMock(return_value=SimpleNamespace(id="other-agent")),
    )
    with pytest.raises(ResourceNotFoundError):
        await endpoints._scope(action.id, interaction.id)
