"""Tests for Meta WhatsApp webhook verification and WhatsAppAction meta config."""

from jvspatial.api.integrations.webhooks.utils import generate_hmac_signature
from starlette.requests import Request

from jvagent.action.utils.meta_webhook import verify_meta_webhook_signature
from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction

AGENT_ID = "n.Agent.test123"
APP_SECRET = "test-app-secret"


def _meta_action(**kwargs) -> WhatsAppAction:
    defaults = {
        "id": "n.WhatsAppAction.meta1",
        "provider": "meta",
        "phone_number_id": "123",
        "access_token": "token",
        "waba_id": "456",
    }
    defaults.update(kwargs)
    return WhatsAppAction(**defaults)


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
    def test_is_configured_meta_requires_jvconnect(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
        monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")

        action = _meta_action(phone_number_id="", access_token="")
        assert action.is_configured() is True
        assert action.is_meta_provider() is True

    def test_is_configured_meta_without_phone_id(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
        monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

        action = _meta_action(phone_number_id="", access_token="")
        assert action.is_configured() is True

    def test_is_configured_meta_missing_jvconnect(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.delenv("JVCONNECT_URL", raising=False)
        monkeypatch.delenv("JVCONNECT_API_KEY", raising=False)
        monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

        action = _meta_action(phone_number_id="", access_token="")
        assert action.is_configured() is False

    def test_is_configured_meta_without_app_secret(self, monkeypatch):
        monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "https://example.com")
        monkeypatch.setenv("JVCONNECT_URL", "https://connect.example.com")
        monkeypatch.setenv("JVCONNECT_API_KEY", "jvk_test")
        monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
        monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)

        action = _meta_action(phone_number_id="", access_token="")
        assert action.is_configured() is True

    def test_parse_webhook_verify_success_jvconnect_token(self, monkeypatch):
        # Meta verifies against jvconnect; agent hub.verify_token is a fixed placeholder
        action = _meta_action()
        result = action.parse_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "jvconnect",
                "hub.challenge": "1234567890",
            },
            agent_id=AGENT_ID,
        )
        assert result == "1234567890"

    def test_parse_webhook_verify_failure(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
        action = _meta_action()
        result = action.parse_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "1234567890",
            },
            agent_id=AGENT_ID,
        )
        assert isinstance(result, dict)
        assert result.get("code") == 403

    def test_parse_webhook_verify_yaml_override(self, monkeypatch):
        monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
        action = _meta_action(verify_token="custom-verify")
        result = action.parse_webhook_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "custom-verify",
                "hub.challenge": "999",
            },
            agent_id=AGENT_ID,
        )
        assert result == "999"

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
