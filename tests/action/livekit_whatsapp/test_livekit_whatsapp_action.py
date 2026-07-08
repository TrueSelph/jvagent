"""Tests for LiveKitWhatsAppAction call handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.livekit_whatsapp.livekit_whatsapp_action import (
    LiveKitWhatsAppAction,
)

_CONNECT_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "calls",
                    "value": {
                        "metadata": {"phone_number_id": "436666719526789"},
                        "contacts": [
                            {
                                "profile": {"name": "Jane Doe"},
                                "wa_id": "16315553601",
                            }
                        ],
                        "calls": [
                            {
                                "id": "wacid.ABGGFjFVU2AfAgo6V",
                                "from": "16315553601",
                                "event": "connect",
                                "session": {
                                    "sdp_type": "offer",
                                    "sdp": "v=0\r\n",
                                },
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


def _action_stub(**overrides: object) -> SimpleNamespace:
    defaults = {
        "livekit_url": "wss://test.livekit.cloud",
        "livekit_api_key": "key",
        "livekit_api_secret": "secret",
        "agent_name": "jvvoice",
        "cloud_api_version": "24.0",
        "room_name_prefix": "whatsapp-call",
        "jvagent_base_url": "",
        "enabled": True,
        "_active_calls": {},
        "_connector": None,
    }
    defaults.update(overrides)
    action = SimpleNamespace(**defaults)
    action._resolved_livekit_url = lambda: (action.livekit_url or "").strip()
    action._resolved_livekit_api_key = lambda: (action.livekit_api_key or "").strip()
    action._resolved_livekit_api_secret = lambda: (
        action.livekit_api_secret or ""
    ).strip()
    for method_name in (
        "_handle_connect",
        "_handle_terminate",
        "_room_name_for_call",
        "_resolved_jvagent_base_url",
        "handle_call_webhook",
    ):
        method = getattr(LiveKitWhatsAppAction, method_name)
        setattr(action, method_name, method.__get__(action, LiveKitWhatsAppAction))
    action._env_jvagent_base_url = lambda: ""
    return action


@pytest.mark.asyncio
async def test_handle_connect_calls_livekit():
    action = _action_stub()
    mock_client = AsyncMock()
    mock_client.accept_whatsapp_call = AsyncMock(
        return_value={"room_name": "whatsapp-call-ago6V", "whatsapp_call_id": "x"}
    )
    action._meta_credentials = AsyncMock(return_value=("436666719526789", "meta-token"))
    action._connector_client = AsyncMock(return_value=mock_client)

    result = await action.handle_call_webhook(_CONNECT_PAYLOAD, agent_id="n.Agent.test")

    assert result["status"] == "connected"
    assert "wacid" in result["call_id"]
    mock_client.accept_whatsapp_call.assert_awaited_once()
    call_kwargs = mock_client.accept_whatsapp_call.await_args.kwargs
    assert call_kwargs["agent_name"] == "jvvoice"
    assert call_kwargs["agent_metadata"]["jvagent_agent_id"] == "n.Agent.test"
    assert call_kwargs["agent_metadata"]["caller_phone"] == "16315553601"


@pytest.mark.asyncio
async def test_handle_connect_includes_jvagent_base_url_in_metadata():
    action = _action_stub()
    action._resolved_jvagent_base_url = lambda: "https://jv.example.com"
    mock_client = AsyncMock()
    mock_client.accept_whatsapp_call = AsyncMock(
        return_value={"room_name": "whatsapp-call-ago6V", "whatsapp_call_id": "x"}
    )
    action._meta_credentials = AsyncMock(return_value=("436666719526789", "meta-token"))
    action._connector_client = AsyncMock(return_value=mock_client)

    await action.handle_call_webhook(_CONNECT_PAYLOAD, agent_id="n.Agent.test")

    call_kwargs = mock_client.accept_whatsapp_call.await_args.kwargs
    assert call_kwargs["agent_metadata"]["jvagent_base_url"] == "https://jv.example.com"


@pytest.mark.asyncio
async def test_handle_terminate_disconnects():
    action = _action_stub()
    action._active_calls = {"wacid.ABGGFjFVU2AfAgo6V": "room-1"}
    mock_client = AsyncMock()
    action._connector_client = AsyncMock(return_value=mock_client)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "calls": [
                                {
                                    "id": "wacid.ABGGFjFVU2AfAgo6V",
                                    "event": "terminate",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    result = await action.handle_call_webhook(payload, agent_id="n.Agent.test")

    assert result["status"] == "disconnected"
    mock_client.disconnect_whatsapp_call.assert_awaited_once()
    assert "wacid.ABGGFjFVU2AfAgo6V" not in action._active_calls


def test_is_configured_requires_credentials():
    action = _action_stub()
    assert LiveKitWhatsAppAction.is_configured(action) is True
    action.livekit_api_key = ""
    with patch.object(LiveKitWhatsAppAction, "_env_livekit_api_key", return_value=""):
        assert LiveKitWhatsAppAction.is_configured(action) is False
