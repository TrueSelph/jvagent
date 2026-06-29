"""Tests for Meta WhatsApp webhook verification and WhatsAppAction meta config."""

import pytest
from jvspatial.api.integrations.webhooks.utils import generate_hmac_signature
from starlette.requests import Request

from jvagent.action.utils.meta_webhook import verify_meta_webhook_signature
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction


def _req_with_sig(body: bytes, signature: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "method": "POST",
            "path": "/",
            "headers": [(b"x-hub-signature-256", signature.encode("ascii"))],
        }
    )


class TestVerifyMetaWebhookSignature:
    def test_valid_signature(self):
        secret = "my-app-secret"
        body = b'{"object":"whatsapp_business_account"}'
        sig = generate_hmac_signature(body, secret)
        req = _req_with_sig(body, sig)
        assert verify_meta_webhook_signature(body, req, secret) is True

    def test_invalid_signature(self):
        body = b'{"test": true}'
        req = _req_with_sig(body, "sha256=deadbeef")
        assert verify_meta_webhook_signature(body, req, "secret") is False

    def test_missing_header(self):
        req = Request(
            {
                "type": "http",
                "asgi": {"spec_version": "2.3", "version": "3.0"},
                "method": "POST",
                "path": "/",
                "headers": [],
            }
        )
        assert verify_meta_webhook_signature(b"{}", req, "secret") is False


class TestWhatsAppActionMetaConfig:
    def test_is_configured_meta_requires_cloud_credentials(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token")
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")

        action = WhatsAppAction(id="n.WhatsAppAction.meta1", provider="meta")
        assert action.is_configured() is True
        assert action.is_meta_provider() is True

    def test_is_configured_meta_missing_verify_token(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token")
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)

        action = WhatsAppAction(id="n.WhatsAppAction.meta2", provider="meta")
        assert action.is_configured() is False

    def test_parse_webhook_verify_success(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "my-verify-token")
        action = WhatsAppAction(id="n.WhatsAppAction.meta3", provider="meta")
        result = action.parse_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "my-verify-token",
                "hub.challenge": "1234567890",
            }
        )
        assert result == "1234567890"

    def test_parse_webhook_verify_failure(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "expected")
        action = WhatsAppAction(id="n.WhatsAppAction.meta4", provider="meta")
        result = action.parse_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "1234567890",
            }
        )
        assert isinstance(result, dict)
        assert result.get("code") == 403

    def test_meta_callback_url_strips_api_key(self):
        url = "https://example.com/api/whatsapp/interact/webhook/agent1?api_key=jv_abc"
        assert (
            WhatsAppAction.meta_callback_url_for_subscription(url)
            == "https://example.com/api/whatsapp/interact/webhook/agent1"
        )

    def test_bridge_provider_still_needs_api_url(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.delenv("WHATSAPP_API_URL", raising=False)
        action = WhatsAppAction(id="n.WhatsAppAction.bridge", provider="wwebjs")
        assert action.is_configured() is False
