"""Tests for Meta WhatsApp Cloud API provider."""

from typing import Optional

import pytest

from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI

SAMPLE_TEXT_WEBHOOK = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "102290129340398",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550783881",
                            "phone_number_id": "106540352242922",
                        },
                        "contacts": [
                            {
                                "profile": {"name": "Sheena Nelson"},
                                "wa_id": "16505551234",
                            }
                        ],
                        "messages": [
                            {
                                "from": "16505551234",
                                "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
                                "timestamp": "1749416383",
                                "type": "text",
                                "text": {"body": "Does it come in another color?"},
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}

STATUS_ONLY_WEBHOOK = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "102290129340398",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550783881",
                            "phone_number_id": "106540352242922",
                        },
                        "statuses": [
                            {
                                "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI3MTE5MjVBOTE3MDk5QUVFM0YA",
                                "status": "delivered",
                                "timestamp": "1750263773",
                                "recipient_id": "16505551234",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}


@pytest.fixture
def meta_api():
    return MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0/",
        session="106540352242922",
        token="test-token",
        phone_number_id="106540352242922",
    )


class TestMetaWhatsAppParseInbound:
    @pytest.mark.asyncio
    async def test_parses_text_message(self, meta_api):
        payload = await meta_api.parse_inbound_message(SAMPLE_TEXT_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "chat"
        assert payload.body == "Does it come in another color?"
        assert payload.sender == "16505551234"
        assert payload.sender_name == "Sheena Nelson"
        assert payload.fromMe is False
        assert payload.isGroup is False

    @pytest.mark.asyncio
    async def test_ignores_status_only_webhook(self, meta_api):
        payload = await meta_api.parse_inbound_message(STATUS_ONLY_WEBHOOK)
        assert payload is not None
        assert payload.message_type == "ignored"

    @pytest.mark.asyncio
    async def test_ignores_wrong_phone_number_id(self, meta_api):
        payload = await meta_api.parse_inbound_message(SAMPLE_TEXT_WEBHOOK)
        meta_api.phone_number_id = "999999999"
        payload2 = await meta_api.parse_inbound_message(SAMPLE_TEXT_WEBHOOK)
        assert payload2 is not None
        assert payload2.message_type == "ignored"
        assert payload.message_type == "chat"

    @pytest.mark.asyncio
    async def test_non_meta_object_returns_none(self, meta_api):
        assert await meta_api.parse_inbound_message({"object": "page"}) is None


class TestMetaWhatsAppSend:
    @pytest.mark.asyncio
    async def test_send_message_builds_graph_payload(self, meta_api):
        captured = {}

        async def fake_request(url, method, headers, data=None, params=None, json_body=True):
            captured["url"] = url
            captured["data"] = data
            return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.x"}]}

        meta_api._make_request = fake_request  # type: ignore[method-assign]

        result = await meta_api.send_message("16505551234", "Hello there")
        assert result.get("ok") is True
        assert captured["url"].endswith("/106540352242922/messages")
        assert captured["data"]["type"] == "text"
        assert captured["data"]["to"] == "16505551234"
        assert captured["data"]["text"]["body"] == "Hello there"

    @pytest.mark.asyncio
    async def test_normalize_recipient_strips_suffix(self, meta_api):
        assert MetaWhatsAppAPI._normalize_recipient("15551234@c.us") == "15551234"
        assert MetaWhatsAppAPI._normalize_recipient("+15551234") == "+15551234"

    @pytest.mark.asyncio
    async def test_register_session_requires_webhook_and_token(self, meta_api):
        result = await meta_api.register_session()
        assert result["ok"] is False
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_register_webhook_subscription_waba(self):
        api = MetaWhatsAppAPI(
            api_url="https://graph.facebook.com/v25.0/",
            session="106540352242922",
            token="test-token",
            phone_number_id="106540352242922",
            waba_id="107732305578216",
            verify_token="jvagent-meta-verify",
        )
        calls: list[tuple[str, Optional[dict]]] = []

        async def fake_send(url, method="POST", data=None, **kwargs):
            calls.append((url, data))
            return {"success": True}

        api.send_rest_request = fake_send  # type: ignore[method-assign]

        result = await api.register_webhook_subscription(
            "https://example.com/api/whatsapp/interact/webhook/n.Agent.x?api_key=secret",
            "jvagent-meta-verify",
        )
        assert result["ok"] is True
        assert "registrations" in result
        assert result["registrations"]["waba"]["ok"] is True
        assert result["registrations"]["phone"]["ok"] is True
        assert len(calls) == 2
        waba_url, waba_data = calls[0]
        phone_url, phone_data = calls[1]
        assert waba_url.endswith("/107732305578216/subscribed_apps")
        assert waba_data["override_callback_uri"] == (
            "https://example.com/api/whatsapp/interact/webhook/n.Agent.x"
        )
        assert waba_data["verify_token"] == "jvagent-meta-verify"
        assert phone_url.endswith("/106540352242922")
        assert phone_data["webhook_configuration"]["override_callback_uri"] == (
            "https://example.com/api/whatsapp/interact/webhook/n.Agent.x"
        )

    @pytest.mark.asyncio
    async def test_register_webhook_subscription_waba_only(self):
        api = MetaWhatsAppAPI(
            api_url="https://graph.facebook.com/v25.0/",
            session="",
            token="test-token",
            phone_number_id="",
            waba_id="107732305578216",
            verify_token="jvagent-meta-verify",
        )
        captured = {}

        async def fake_send(url, method="POST", data=None, **kwargs):
            captured["url"] = url
            captured["data"] = data
            return {"success": True}

        api.send_rest_request = fake_send  # type: ignore[method-assign]

        result = await api.register_webhook_subscription(
            "https://example.com/callback", "verify-me"
        )
        assert result["ok"] is True
        assert "phone" not in result["registrations"]
        assert captured["url"].endswith("/107732305578216/subscribed_apps")

    @pytest.mark.asyncio
    async def test_register_webhook_subscription_phone_fallback(self, meta_api):
        captured = {}

        async def fake_send(url, method="POST", data=None, **kwargs):
            captured["url"] = url
            captured["data"] = data
            captured["method"] = method
            return {"success": True}

        meta_api.send_rest_request = fake_send  # type: ignore[method-assign]

        result = await meta_api.register_webhook_subscription(
            "https://example.com/callback", "verify-me"
        )
        assert result["ok"] is True
        assert captured["url"].endswith("/106540352242922")
        assert captured["data"]["webhook_configuration"]["override_callback_uri"] == (
            "https://example.com/callback"
        )

    @pytest.mark.asyncio
    async def test_register_webhook_subscribes_waba_before_override(self):
        api = MetaWhatsAppAPI(
            api_url="https://graph.facebook.com/v25.0/",
            session="106540352242922",
            token="test-token",
            phone_number_id="106540352242922",
            waba_id="107732305578216",
            verify_token="jvagent-meta-verify",
        )
        calls: list[tuple[str, Optional[dict]]] = []

        async def fake_send(url, method="POST", data=None, **kwargs):
            calls.append((method, data))
            if len(calls) == 1:
                return {
                    "ok": False,
                    "error": "HTTP 400: (#100) Before override the current callback uri",
                }
            return {"success": True}

        api.send_rest_request = fake_send  # type: ignore[method-assign]

        result = await api.register_webhook_subscription(
            "https://example.com/callback", "verify-me"
        )
        assert result["ok"] is True
        assert len(calls) == 4
        assert calls[0][1] is not None
        assert calls[1][1] is None
        assert calls[2][1]["override_callback_uri"] == "https://example.com/callback"
        assert "webhook_configuration" in (calls[3][1] or {})
