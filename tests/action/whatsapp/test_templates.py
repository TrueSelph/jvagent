"""Tests for WhatsApp Meta template tools (list/send + channel gate)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.whatsapp.modules.jvconnect_api import JvconnectWhatsAppAPI
from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.tooling.tool_executor import ToolDispatchContext, bind_dispatch_context


def _meta_action(**attrs) -> WhatsAppAction:
    action = WhatsAppAction()
    object.__setattr__(action, "provider", "meta")
    object.__setattr__(action, "template_allowlist", [])
    object.__setattr__(action, "default_template_language", "en_US")
    for k, v in attrs.items():
        object.__setattr__(action, k, v)
    return action


@pytest.mark.asyncio
async def test_send_template_rejects_non_whatsapp_channel(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    visitor = SimpleNamespace(
        user_id="15551234567",
        channel="default",
        session_id="s1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.send_template("signup"))
    assert out["ok"] is False
    assert out["error"] == "whatsapp_templates_require_inbound_whatsapp"


@pytest.mark.asyncio
async def test_send_template_rejects_missing_context():
    action = _meta_action()
    out = json.loads(await action.send_template("signup"))
    assert out["ok"] is False
    assert out["error"] == "whatsapp_templates_require_inbound_whatsapp"


@pytest.mark.asyncio
async def test_send_template_forces_recipient_to_user_id(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action(template_allowlist=["signup"])
    sent = {}

    async def fake_api(self):
        api = SimpleNamespace()

        async def send_template_message(phone, name, language="en_US", components=None):
            sent["phone"] = phone
            sent["name"] = name
            sent["language"] = language
            sent["components"] = components
            return {
                "ok": True,
                "messages": [{"id": "wamid.tmpl"}],
            }

        api.send_template_message = send_template_message
        return api

    monkeypatch.setattr(WhatsAppAction, "api", fake_api)
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)

    visitor = SimpleNamespace(
        user_id="15559876543",
        channel="whatsapp",
        session_id="wa-1",
        interaction=SimpleNamespace(
            add_parameter=lambda *a, **k: True,
            save=AsyncMock(),
        ),
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.send_template("signup"))
    assert out["ok"] is True
    assert sent["phone"] == "15559876543"
    assert sent["name"] == "signup"
    assert out["to"] == "15559876543"
    assert out["message_id"] == "wamid.tmpl"


@pytest.mark.asyncio
async def test_send_template_allowlist_rejection(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action(template_allowlist=["signup"])
    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.send_template("promo_blast"))
    assert out["ok"] is False
    assert "allowlist" in out["error"]


@pytest.mark.asyncio
async def test_list_templates_filters_allowlist(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action(template_allowlist=["signup"])

    async def fake_api(self):
        api = SimpleNamespace()

        async def list_message_templates():
            return {
                "ok": True,
                "templates": [
                    {
                        "name": "signup",
                        "language": "en_US",
                        "status": "APPROVED",
                        "category": "UTILITY",
                    },
                    {
                        "name": "promo",
                        "language": "en_US",
                        "status": "APPROVED",
                        "category": "MARKETING",
                    },
                ],
            }

        api.list_message_templates = list_message_templates
        return api

    monkeypatch.setattr(WhatsAppAction, "api", fake_api)
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)

    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.list_templates())
    assert out["ok"] is True
    assert len(out["templates"]) == 1
    assert out["templates"][0]["name"] == "signup"


@pytest.mark.asyncio
async def test_meta_send_template_message_payload(monkeypatch):
    api = MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0",
        session="phone1",
        token="tok",
        phone_number_id="phone1",
        waba_id="waba1",
    )
    called = {}

    async def fake_send_rest(endpoint, method="POST", data=None, **kwargs):
        called["endpoint"] = endpoint
        called["method"] = method
        called["data"] = data
        return {"ok": True, "messages": [{"id": "wamid.1"}]}

    monkeypatch.setattr(api, "send_rest_request", fake_send_rest)
    result = await api.send_template_message(
        "15551112222",
        "signup",
        language="en_US",
        components=[{"type": "body", "parameters": [{"type": "text", "text": "Jane"}]}],
    )
    assert result["ok"] is True
    assert called["data"]["type"] == "template"
    assert called["data"]["to"] == "15551112222"
    assert called["data"]["template"]["name"] == "signup"
    assert called["data"]["template"]["language"]["code"] == "en_US"
    assert len(called["data"]["template"]["components"]) == 1


@pytest.mark.asyncio
async def test_jvconnect_list_message_templates(monkeypatch):
    api = JvconnectWhatsAppAPI(
        api_url="https://connect.example.com",
        session="jvconnect",
        token="jvk_x",
    )
    api._account_loaded = True
    api.phone_number_id = "phone1"
    api.waba_id = "waba1"

    async def fake_json(method, path, json_body=None, params=None):
        assert method == "GET"
        assert path == "templates"
        return {
            "ok": True,
            "templates": [
                {"name": "signup", "language": "en_US", "status": "APPROVED"}
            ],
        }

    monkeypatch.setattr(api, "_jvconnect_json", fake_json)
    result = await api.list_message_templates()
    assert result["ok"] is True
    assert result["templates"][0]["name"] == "signup"


@pytest.mark.asyncio
async def test_jvconnect_send_template_routes_messages(monkeypatch):
    api = JvconnectWhatsAppAPI(
        api_url="https://connect.example.com",
        session="jvconnect",
        token="jvk_x",
        phone_number_id="phone1",
    )
    api._account_loaded = True
    called = {}

    async def fake_json(method, path, json_body=None, params=None):
        called["method"] = method
        called["path"] = path
        called["json_body"] = json_body
        return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.t"}]}

    monkeypatch.setattr(api, "_jvconnect_json", fake_json)
    result = await api.send_template_message("15550001111", "signup")
    assert called["path"] == "messages"
    assert called["json_body"]["message"]["type"] == "template"
    assert called["json_body"]["message"]["template"]["name"] == "signup"
    assert result.get("ok") is True


def test_tool_names_use_whatsapp_namespace():
    action = _meta_action()
    from jvagent.tooling.tool_decorator import collect_tools

    names = {t.name for t in collect_tools(action)}
    assert "whatsapp__list_templates" in names
    assert "whatsapp__send_template" in names


def test_dispatch_context_carries_channel():
    visitor = SimpleNamespace(
        user_id="u1",
        channel="whatsapp",
        session_id="s",
        interaction=None,
        _agent=SimpleNamespace(id="a"),
    )
    with bind_dispatch_context(visitor):
        from jvagent.tooling.tool_executor import get_dispatch_context

        ctx = get_dispatch_context()
        assert isinstance(ctx, ToolDispatchContext)
        assert ctx.channel == "whatsapp"
        assert ctx.user_id == "u1"
