"""Tests for WhatsAppVoiceAction call handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.whatsapp.modules.jvconnect_api import JvconnectWhatsAppAPI
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
        "_meta_credentials",
        "_resolve_call_session_id",
        "_get_whatsapp_action",
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
    action._resolve_call_session_id = AsyncMock(return_value="sess_abc123def456")
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
    assert payload["session_id"] == "sess_abc123def456"
    assert payload["user_id"] == "16315553601"
    assert "room_name" not in payload
    action._resolve_call_session_id.assert_awaited_once_with(
        "16315553601", agent_id="n.Agent.test"
    )


@pytest.mark.asyncio
async def test_meta_credentials_uses_env_override():
    action = _action_stub()
    wa = SimpleNamespace(
        _env_phone_number_id=lambda: "phone_from_env",
        _env_access_token=lambda: "token_from_env",
        api=AsyncMock(),
    )
    action._get_whatsapp_action = AsyncMock(return_value=wa)

    phone, token = await WhatsAppVoiceAction._meta_credentials(action)

    assert phone == "phone_from_env"
    assert token == "token_from_env"
    wa.api.assert_not_awaited()


@pytest.mark.asyncio
async def test_meta_credentials_fetches_from_jvconnect():
    action = _action_stub()
    jv_client = MagicMock(spec=JvconnectWhatsAppAPI)
    jv_client.fetch_calling_credentials = AsyncMock(
        return_value={
            "ok": True,
            "phone_number_id": "phone_jv",
            "access_token": "token_jv",
        }
    )
    wa = SimpleNamespace(
        _env_phone_number_id=lambda: "",
        _env_access_token=lambda: "",
        api=AsyncMock(return_value=jv_client),
    )
    action._get_whatsapp_action = AsyncMock(return_value=wa)

    phone, token = await WhatsAppVoiceAction._meta_credentials(action)

    assert phone == "phone_jv"
    assert token == "token_jv"
    jv_client.fetch_calling_credentials.assert_awaited_once()


@pytest.mark.asyncio
async def test_meta_credentials_raises_when_unavailable():
    action = _action_stub()
    wa = SimpleNamespace(
        _env_phone_number_id=lambda: "",
        _env_access_token=lambda: "",
        api=AsyncMock(return_value=SimpleNamespace()),  # not JvconnectWhatsAppAPI
    )
    action._get_whatsapp_action = AsyncMock(return_value=wa)

    with pytest.raises(ValueError, match="calling/credentials"):
        await WhatsAppVoiceAction._meta_credentials(action)


@pytest.mark.asyncio
async def test_resolve_call_session_id_reuses_active_conversation():
    action = _action_stub()
    convo = SimpleNamespace(session_id="sess_existing")
    with patch(
        "jvagent.action.whatsapp_voice.whatsapp_voice_action.get_conversation_with_lock",
        new=AsyncMock(return_value=convo),
    ):
        session_id = await WhatsAppVoiceAction._resolve_call_session_id(
            action, "16315553601", agent_id="n.Agent.test"
        )
    assert session_id == "sess_existing"


@pytest.mark.asyncio
async def test_resolve_call_session_id_creates_whatsapp_conversation():
    action = _action_stub()
    created = SimpleNamespace(session_id="sess_created")
    user = SimpleNamespace(create_conversation=AsyncMock(return_value=created))
    memory = SimpleNamespace(get_user=AsyncMock(return_value=user))
    agent = SimpleNamespace(get_memory=AsyncMock(return_value=memory))
    with (
        patch(
            "jvagent.action.whatsapp_voice.whatsapp_voice_action.get_conversation_with_lock",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new=AsyncMock(return_value=agent),
        ),
    ):
        session_id = await WhatsAppVoiceAction._resolve_call_session_id(
            action, "16315553601", agent_id="n.Agent.test"
        )
    assert session_id == "sess_created"
    user.create_conversation.assert_awaited_once_with(channel="whatsapp")


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


@pytest.mark.asyncio
async def test_handle_terminate_passes_meta_token():
    """User-initiated disconnect forwards the Meta access token to jvvoice."""
    from jvagent.action.whatsapp_voice.call_webhook import WhatsAppCallEvent

    action = _action_stub()
    action._meta_credentials = AsyncMock(return_value=("436666719526789", "meta-token"))
    mock_client = AsyncMock()
    mock_client.disconnect_call = AsyncMock(return_value={"status": "disconnected"})
    action._jvvoice_client = AsyncMock(return_value=mock_client)

    event = WhatsAppCallEvent(
        call_id="wacid.ABGGFjFVU2AfAgo6V",
        event="terminate",
        sdp="",
        sdp_type="",
        phone_number_id="436666719526789",
        from_number="16315553601",
        to_number="",
        contact_name="",
    )
    result = await action._handle_terminate(event)

    assert result["status"] == "disconnected"
    kwargs = mock_client.disconnect_call.await_args.kwargs
    assert kwargs["whatsapp_api_key"] == "meta-token"
    assert kwargs["user_initiated"] is True


@pytest.mark.asyncio
async def test_handle_terminate_survives_missing_credentials():
    """Disconnect still delegates (with empty token) if credentials fail."""
    from jvagent.action.whatsapp_voice.call_webhook import WhatsAppCallEvent

    action = _action_stub()
    action._meta_credentials = AsyncMock(side_effect=ValueError("no creds"))
    mock_client = AsyncMock()
    mock_client.disconnect_call = AsyncMock(return_value={"status": "disconnected"})
    action._jvvoice_client = AsyncMock(return_value=mock_client)

    event = WhatsAppCallEvent(
        call_id="wacid.X",
        event="terminate",
        sdp="",
        sdp_type="",
        phone_number_id="",
        from_number="16315553601",
        to_number="",
        contact_name="",
    )
    result = await action._handle_terminate(event)

    assert result["status"] == "disconnected"
    kwargs = mock_client.disconnect_call.await_args.kwargs
    assert kwargs["whatsapp_api_key"] == ""


def test_is_configured_requires_jvvoice_credentials():
    action = _action_stub()
    assert WhatsAppVoiceAction.is_configured(action) is True
    action.jvvoice_api_key = ""
    assert WhatsAppVoiceAction.is_configured(action) is False
