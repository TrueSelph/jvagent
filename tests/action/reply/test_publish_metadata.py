"""Native receipt metadata crosses the real ReplyAction egress contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.reply import ReplyAction


@pytest.mark.asyncio
async def test_server_metadata_overrides_client_flags_without_changing_recipient() -> (
    None
):
    bus = SimpleNamespace(
        publish=AsyncMock(return_value=SimpleNamespace(content="Answer"))
    )
    interaction = SimpleNamespace(id="interaction", user_id="owner")
    visitor = SimpleNamespace(
        session_id="session",
        channel="default",
        interaction=interaction,
        response_bus=bus,
        data={"is_proactive": True, "user_id": "forged"},
    )
    receipt = {"is_proactive": False, "observability": {"usage": {"total_tokens": 12}}}
    assert await ReplyAction().publish(
        "Answer", visitor, message_id="o.ResponseMessage.test", metadata=receipt
    )
    options = bus.publish.call_args.kwargs
    assert options["metadata"]["is_proactive"] is False
    assert options["metadata"]["observability"] == receipt["observability"]
    assert options["user_id"] == "owner" and options["session_id"] == "session"


@pytest.mark.asyncio
async def test_native_request_metadata_cannot_invoke_adapter_escape_hatches() -> None:
    bus = SimpleNamespace(
        publish=AsyncMock(return_value=SimpleNamespace(content="Answer"))
    )
    visitor = SimpleNamespace(
        _native_execution=True,
        session_id="session",
        channel="whatsapp",
        interaction=SimpleNamespace(id="interaction", user_id="owner"),
        response_bus=bus,
        data={
            "whatsapp_cloud_message": {
                "type": "template",
                "template": {"name": "forbidden"},
            },
            "media_url": "https://untrusted.invalid/private",
            "native_local_only": False,
        },
    )
    receipt = {"native_local_only": True, "observability": {"usage": {}}}
    assert await ReplyAction().publish(
        "Answer", visitor, message_id="reply", metadata=receipt
    )
    options = bus.publish.call_args.kwargs
    assert options["metadata"] == receipt
    assert options["require_adapter_ack"] is False
