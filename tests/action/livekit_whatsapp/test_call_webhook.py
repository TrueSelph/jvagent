"""Tests for Meta WhatsApp call webhook parsing."""

from jvagent.action.livekit_whatsapp.call_webhook import (
    is_calls_webhook,
    parse_calls_webhook,
)

_CONNECT_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "calls",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "phone_number_id": "436666719526789",
                            "display_phone_number": "13175551399",
                        },
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
                                "to": "13175551399",
                                "event": "connect",
                                "timestamp": "1671644824",
                                "session": {
                                    "sdp_type": "offer",
                                    "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n",
                                },
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

_TERMINATE_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "calls",
                    "value": {
                        "metadata": {"phone_number_id": "436666719526789"},
                        "calls": [
                            {
                                "id": "wacid.ABGGFjFVU2AfAgo6V",
                                "from": "16315553601",
                                "event": "terminate",
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

_MESSAGES_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {"messages": [{"id": "wamid.abc", "type": "text"}]},
                }
            ],
        }
    ],
}


class TestIsCallsWebhook:
    def test_detects_calls_field(self):
        assert is_calls_webhook(_CONNECT_PAYLOAD) is True

    def test_ignores_messages_field(self):
        assert is_calls_webhook(_MESSAGES_PAYLOAD) is False

    def test_ignores_non_waba_object(self):
        assert is_calls_webhook({"object": "page", "entry": []}) is False


class TestParseCallsWebhook:
    def test_parse_connect_event(self):
        events = parse_calls_webhook(_CONNECT_PAYLOAD)
        assert len(events) == 1
        ev = events[0]
        assert ev.call_id == "wacid.ABGGFjFVU2AfAgo6V"
        assert ev.event == "connect"
        assert ev.sdp_type == "offer"
        assert "v=0" in ev.sdp
        assert ev.phone_number_id == "436666719526789"
        assert ev.from_number == "16315553601"
        assert ev.contact_name == "Jane Doe"

    def test_parse_terminate_event(self):
        events = parse_calls_webhook(_TERMINATE_PAYLOAD)
        assert len(events) == 1
        assert events[0].event == "terminate"
        assert events[0].call_id == "wacid.ABGGFjFVU2AfAgo6V"

    def test_empty_when_no_calls(self):
        assert parse_calls_webhook(_MESSAGES_PAYLOAD) == []
