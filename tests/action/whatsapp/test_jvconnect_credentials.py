"""Tests for jvconnect Meta WhatsApp transport on WhatsAppAction."""

import pytest

from jvagent.action.whatsapp.modules.jvconnect_api import JvconnectWhatsAppAPI
from jvagent.action.whatsapp.modules.registry import get_provider_factory
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction


def test_jvconnect_provider_registered():
    assert get_provider_factory("jvconnect") is JvconnectWhatsAppAPI


def test_meta_config_issues_without_proxy(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.delenv("JVCONNECT_URL", raising=False)
    monkeypatch.delenv("JVCONNECT_API_KEY", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    action = WhatsAppAction()
    object.__setattr__(action, "provider", "meta")
    object.__setattr__(action, "phone_number_id", "123")
    issues = action._config_issues()
    assert any("JVCONNECT_URL" in i or "jvconnect_url" in i for i in issues)
    assert any("JVCONNECT_API_KEY" in i for i in issues)
    assert action.is_configured() is False


def test_meta_configured_with_jvconnect_only(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    action = WhatsAppAction()
    object.__setattr__(action, "provider", "meta")
    object.__setattr__(action, "phone_number_id", "123")
    assert action.is_configured() is True
    assert action._config_issues() == []


@pytest.mark.asyncio
async def test_jvconnect_api_routes_messages(monkeypatch):
    api = JvconnectWhatsAppAPI(
        api_url="https://connect.example.com",
        session="phone1",
        token="jvk_x",
        phone_number_id="phone1",
        waba_id="waba1",
    )
    assert "/api/v1/meta/whatsapp/" in api._v1("messages")
    called = {}

    async def fake_json(method, path, json_body=None, params=None):
        called["method"] = method
        called["path"] = path
        called["json_body"] = json_body
        return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.1"}]}

    monkeypatch.setattr(api, "_jvconnect_json", fake_json)
    result = await api.send_rest_request(
        "https://graph.facebook.com/v25.0/phone1/messages",
        method="POST",
        data={"type": "text", "text": {"body": "hi"}},
        use_full_url=True,
    )
    assert called["path"] == "messages"
    assert called["json_body"]["phone_number_id"] == "phone1"
    assert result.get("ok") is True


def test_env_app_secret_uses_jvconnect_webhook_secret(monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    action = WhatsAppAction()
    object.__setattr__(action, "provider", "meta")
    object.__setattr__(action, "jvconnect_webhook_secret", "jvs_secret")
    assert action._env_app_secret() == "jvs_secret"
