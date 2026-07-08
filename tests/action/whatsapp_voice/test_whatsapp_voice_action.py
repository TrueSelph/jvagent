"""Tests for WhatsAppVoiceAction call handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.whatsapp_voice.whatsapp_voice_action import WhatsAppVoiceAction

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
        "jvvoice_base_url": "https://jvvoice.example.com",
        "jvvoice_api_key": "secret",
        "agent_name": "jvvoice",
        "cloud_api_version": "24.0",
        "jvagent_base_url": "",
        "enabled": True,
        "_active_calls": {},
        "_jvvoice": None,
    }
    defaults.update(overrides)
    action = SimpleNamespace(**defaults)
    for method_name in (
        "_handle_connect",
        "_handle_terminate",
        "_resolved_jvagent_base_url",
        "_resolved_jvvoice_base_url",
        "_resolved_jvvoice_api_key",
        "handle_call_webhook",
        "is_configured",
    ):
        method = getattr(WhatsAppVoiceAction, method_name)
        setattr(action, method_name, method.__get__(action, WhatsAppVoiceAction))
    action._env_jvagent_base_url = lambda: ""
    action._env_jvvoice_base_url = lambda: ""
    action._env_jvvoice_api_key = lambda: ""
    return action


@pytest.mark.asyncio
async def test_handle_connect_delegates_to_jvvoice():
    action = _action_stub()
    action._resolved_jvagent_base_url = lambda: "https://jv.example.com"
    mock_client = AsyncMock()
    mock_client.accept_call = AsyncMock(
        return_value={
            "status": "connected",
            "call_id": "wacid.ABGGFjFVU2AfAgo6V",
            "room_name": "whatsapp-call-ago6V",
        }
    )
    action._meta_credentials = AsyncMock(return_value=("436666719526789", "meta-token"))
    action._jvvoice_client = AsyncMock(return_value=mock_client)

    result = await action.handle_call_webhook(_CONNECT_PAYLOAD, agent_id="n.Agent.test")

    assert result["status"] == "connected"
    assert "wacid" in result["call_id"]
    mock_client.accept_call.assert_awaited_once()
    payload = mock_client.accept_call.await_args.args[0]
    assert payload["agent_name"] == "jvvoice"
    assert payload["jvagent_agent_id"] == "n.Agent.test"
    assert payload["jvagent_base_url"] == "https://jv.example.com"
    assert payload["caller_phone"] == "16315553601"
    assert "room_name" not in payload


@pytest.mark.asyncio
async def test_handle_terminate_delegates_to_jvvoice():
    action = _action_stub()
    action._active_calls = {"wacid.ABGGFjFVU2AfAgo6V": "room-1"}
    mock_client = AsyncMock()
    mock_client.disconnect_call = AsyncMock(return_value={"status": "disconnected"})
    action._jvvoice_client = AsyncMock(return_value=mock_client)

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
    mock_client.disconnect_call.assert_awaited_once()
    assert "wacid.ABGGFjFVU2AfAgo6V" not in action._active_calls


def test_is_configured_requires_jvvoice_credentials():
    action = _action_stub()
    assert WhatsAppVoiceAction.is_configured(action) is True
    action.jvvoice_api_key = ""
    assert WhatsAppVoiceAction.is_configured(action) is False
