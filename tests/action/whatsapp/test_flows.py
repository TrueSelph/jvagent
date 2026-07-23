"""Tests for WhatsApp Flow tools (list/send + channel gate)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.whatsapp.modules.jvconnect_api import JvconnectWhatsAppAPI
from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.tooling.tool_decorator import collect_tools
from jvagent.tooling.tool_executor import bind_dispatch_context


def _meta_action(**attrs) -> WhatsAppAction:
    action = WhatsAppAction()
    object.__setattr__(action, "provider", "meta")
    object.__setattr__(action, "template_allowlist", [])
    object.__setattr__(action, "flow_allowlist", [])
    object.__setattr__(action, "default_template_language", "en_US")
    for k, v in attrs.items():
        object.__setattr__(action, k, v)
    return action


@pytest.mark.asyncio
async def test_send_flow_rejects_non_whatsapp_channel(monkeypatch):
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
        out = json.loads(await action.send_flow(flow_id="flow_1"))
    assert out["ok"] is False
    assert out["error"] == "whatsapp_flows_require_inbound_whatsapp"


@pytest.mark.asyncio
async def test_send_flow_allows_whatsapp_call_channel(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    sent = {}

    async def fake_api(self):
        api = SimpleNamespace()

        async def send_flow_message(phone, **kwargs):
            sent["phone"] = phone
            sent.update(kwargs)
            return {"ok": True, "messages": [{"id": "wamid.flow"}]}

        api.send_flow_message = send_flow_message
        return api

    monkeypatch.setattr(WhatsAppAction, "api", fake_api)
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)
    visitor = SimpleNamespace(
        user_id="15559876543",
        channel="whatsapp_call",
        session_id="wa-call-1",
        interaction=SimpleNamespace(
            add_parameter=lambda *a, **k: True,
            save=AsyncMock(),
        ),
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.send_flow(flow_id="flow_9", body="Hi"))
    assert out["ok"] is True
    assert sent["phone"] == "15559876543"
    assert out["to"] == "15559876543"


@pytest.mark.asyncio
async def test_send_flow_forces_recipient(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    sent = {}

    async def fake_api(self):
        api = SimpleNamespace()

        async def send_flow_message(phone, **kwargs):
            sent["phone"] = phone
            sent.update(kwargs)
            return {"ok": True, "messages": [{"id": "wamid.flow"}]}

        api.send_flow_message = send_flow_message
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
        out = json.loads(await action.send_flow(flow_id="flow_9", body="Hi"))
    assert out["ok"] is True
    assert sent["phone"] == "15559876543"
    assert sent["flow_id"] == "flow_9"
    assert out["to"] == "15559876543"


@pytest.mark.asyncio
async def test_send_flow_allowlist_rejection(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action(flow_allowlist=["allowed_flow"])
    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(await action.send_flow(flow_name="blocked_flow"))
    assert out["ok"] is False
    assert "allowlist" in out["error"]
    assert "hint" in out


@pytest.mark.asyncio
async def test_send_flow_allowlist_accepts_id_when_listed(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action(flow_allowlist=["ai_readiness_check", "1663253904764206"])
    sent = {}

    async def fake_api(self):
        api = SimpleNamespace()

        async def send_flow_message(phone, **kwargs):
            sent.update(kwargs)
            return {"ok": True, "messages": [{"id": "wamid.flow"}]}

        api.send_flow_message = send_flow_message
        return api

    monkeypatch.setattr(WhatsAppAction, "api", fake_api)
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)
    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=SimpleNamespace(
            add_parameter=lambda *a, **k: True,
            save=AsyncMock(),
        ),
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        # id-only must pass when id is on the allowlist (name-only would fail)
        out = json.loads(await action.send_flow(flow_id="1663253904764206"))
    assert out["ok"] is True
    assert sent["flow_id"] == "1663253904764206"


@pytest.mark.asyncio
async def test_send_flow_forwards_screen_data(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    sent = {}

    async def fake_api(self):
        api = SimpleNamespace()

        async def send_flow_message(phone, **kwargs):
            sent["phone"] = phone
            sent.update(kwargs)
            return {"ok": True, "messages": [{"id": "wamid.flow"}]}

        api.send_flow_message = send_flow_message
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
        out = json.loads(
            await action.send_flow(
                flow_id="flow_9",
                screen="PROFILE_SCREEN",
                screen_data={"name": "Jason", "order_id": "123"},
            )
        )
    assert out["ok"] is True
    assert sent["flow_action"] == "navigate"
    assert sent["screen"] == "PROFILE_SCREEN"
    assert sent["flow_action_data"] == {"name": "Jason", "order_id": "123"}


@pytest.mark.asyncio
async def test_send_flow_screen_data_requires_screen(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)
    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(
            await action.send_flow(
                flow_id="flow_1",
                screen_data={"name": "Jason"},
            )
        )
    assert out["ok"] is False
    assert "screen is required" in out["error"]


@pytest.mark.asyncio
async def test_send_flow_screen_data_rejects_data_exchange(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://agent.example.com")
    monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
    monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
    action = _meta_action()
    monkeypatch.setattr(WhatsAppAction, "is_configured", lambda self: True)
    visitor = SimpleNamespace(
        user_id="15551112222",
        channel="whatsapp",
        session_id="wa-1",
        interaction=None,
        _agent=SimpleNamespace(id="a1"),
    )
    with bind_dispatch_context(visitor):
        out = json.loads(
            await action.send_flow(
                flow_id="flow_1",
                flow_action="data_exchange",
                screen="WELCOME_SCREEN",
                screen_data={"name": "Jason"},
            )
        )
    assert out["ok"] is False
    assert "data_exchange" in out["error"]


@pytest.mark.asyncio
async def test_meta_send_flow_message_payload(monkeypatch):
    api = MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0",
        session="phone1",
        token="tok",
        phone_number_id="phone1",
        waba_id="waba1",
    )
    called = {}

    async def fake_send_rest(endpoint, method="POST", data=None, **kwargs):
        called["data"] = data
        return {"ok": True, "messages": [{"id": "wamid.1"}]}

    monkeypatch.setattr(api, "send_rest_request", fake_send_rest)
    result = await api.send_flow_message(
        "15551112222",
        flow_id="flow_1",
        flow_cta="Open",
        body="Please complete this form.",
        flow_action="navigate",
        screen="WELCOME_SCREEN",
        flow_action_data={"name": "Jason", "order_id": "12345"},
    )
    assert result["ok"] is True
    assert called["data"]["type"] == "interactive"
    assert called["data"]["interactive"]["type"] == "flow"
    params = called["data"]["interactive"]["action"]["parameters"]
    assert params["flow_id"] == "flow_1"
    assert params["flow_action"] == "navigate"
    assert params["flow_action_payload"]["screen"] == "WELCOME_SCREEN"
    assert params["flow_action_payload"]["data"] == {
        "name": "Jason",
        "order_id": "12345",
    }


@pytest.mark.asyncio
async def test_meta_send_cta_url_message_payload(monkeypatch):
    api = MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0",
        session="phone1",
        token="tok",
        phone_number_id="phone1",
        waba_id="waba1",
    )
    called = {}

    async def fake_send_rest(endpoint, method="POST", data=None, **kwargs):
        called["data"] = data
        return {"ok": True, "messages": [{"id": "wamid.cta"}]}

    monkeypatch.setattr(api, "send_rest_request", fake_send_rest)
    result = await api.send_cta_url_message(
        "15551112222",
        url="https://mmgpg.example/pay?token=abc",
        body="Tap Pay now to complete payment for invoice Z1.",
        display_text="Pay now",
        footer="One-time link",
    )
    assert result["ok"] is True
    assert called["data"]["type"] == "interactive"
    assert called["data"]["interactive"]["type"] == "cta_url"
    params = called["data"]["interactive"]["action"]["parameters"]
    assert params["display_text"] == "Pay now"
    assert params["url"] == "https://mmgpg.example/pay?token=abc"
    assert called["data"]["interactive"]["footer"]["text"] == "One-time link"


@pytest.mark.asyncio
async def test_jvconnect_list_and_send_flow(monkeypatch):
    api = JvconnectWhatsAppAPI(
        api_url="https://connect.example.com",
        session="jvconnect",
        token="jvk_x",
        phone_number_id="phone1",
    )
    api._account_loaded = True
    calls = []

    async def fake_json(method, path, json_body=None, params=None):
        calls.append((method, path, json_body))
        if path == "flows":
            return {
                "ok": True,
                "flows": [{"id": "f1", "name": "signup_form", "status": "PUBLISHED"}],
            }
        return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.f"}]}

    monkeypatch.setattr(api, "_jvconnect_json", fake_json)
    listed = await api.list_flows()
    assert listed["ok"] is True
    assert listed["flows"][0]["name"] == "signup_form"
    sent = await api.send_flow_message("15550001111", flow_id="f1", flow_cta="Open")
    assert sent.get("ok") is True
    assert any(c[1] == "messages" for c in calls)
    msg = [c for c in calls if c[1] == "messages"][0][2]["message"]
    assert msg["type"] == "interactive"
    assert msg["interactive"]["type"] == "flow"


def test_flow_tool_names():
    action = _meta_action()
    names = {t.name for t in collect_tools(action)}
    assert "whatsapp__list_flows" in names
    assert "whatsapp__send_flow" in names
    assert "whatsapp__list_templates" in names
