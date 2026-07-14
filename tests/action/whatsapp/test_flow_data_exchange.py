"""Tests for jvconnect Flow data-exchange helpers."""

from jvagent.action.whatsapp.utils.flow_data_exchange import (
    build_flow_data_exchange_response,
    is_flow_data_exchange_request,
)


class TestFlowDataExchangeDetect:
    def test_detects_header(self):
        assert is_flow_data_exchange_request(
            {"X-Jvconnect-Flow-Exchange": "1"}, {"type": "other"}
        )

    def test_detects_body_type(self):
        assert is_flow_data_exchange_request(
            {}, {"type": "whatsapp_flow_data_exchange", "action": "INIT"}
        )

    def test_rejects_plain_meta_webhook(self):
        assert not is_flow_data_exchange_request(
            {}, {"object": "whatsapp_business_account"}
        )


class TestFlowDataExchangeResponse:
    def test_ping(self):
        assert build_flow_data_exchange_response({"action": "ping"}) == {
            "data": {"status": "active"}
        }

    def test_init_returns_not_configured(self):
        out = build_flow_data_exchange_response(
            {
                "type": "whatsapp_flow_data_exchange",
                "action": "INIT",
                "flow_token": "ft1",
            }
        )
        assert out["data"]["error"] == "endpoint_not_configured"

    def test_data_exchange_without_screen_returns_not_configured(self):
        out = build_flow_data_exchange_response(
            {"action": "data_exchange", "flow_token": "ft1"}
        )
        assert out["data"]["error"] == "endpoint_not_configured"

    def test_complete_returns_success_screen(self):
        out = build_flow_data_exchange_response(
            {
                "action": "data_exchange",
                "screen": "REVIEW",
                "flow_token": "ft1",
                "data": {"name": "Ada"},
            }
        )
        assert out["screen"] == "SUCCESS"
        assert out["data"]["extension_message_response"]["params"]["flow_token"] == "ft1"
        assert out["data"]["extension_message_response"]["params"]["name"] == "Ada"
