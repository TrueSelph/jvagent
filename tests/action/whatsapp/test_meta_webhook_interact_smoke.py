"""Smoke tests for Meta whatsapp_interact webhook dedup (production parity)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.integrations.webhooks.utils import generate_hmac_signature
from starlette.requests import Request

from jvagent.action.utils.meta_webhook_dedup import clear_meta_wamid_cache
from jvagent.action.whatsapp.endpoints import whatsapp_interact

from .test_meta_api import SAMPLE_TEXT_WEBHOOK

WAMID = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA="
APP_SECRET = "test-app-secret"
AGENT_ID = "n.Agent.smoke"


def _meta_post_request(body: dict) -> Request:
    raw = json.dumps(body).encode("utf-8")
    sig = generate_hmac_signature(raw, APP_SECRET)
    return Request(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "method": "POST",
            "path": f"/api/whatsapp/interact/webhook/{AGENT_ID}",
            "headers": [(b"x-hub-signature-256", sig.encode("ascii"))],
        }
    )


@pytest.fixture(autouse=True)
def _clear_dedup():
    clear_meta_wamid_cache()
    yield
    clear_meta_wamid_cache()


@pytest.fixture
def mock_meta_webhook_stack():
    """Minimal mocks so first webhook consumes wamid without running orchestrator."""
    agent = MagicMock()
    agent.get_access_control_action = AsyncMock(return_value=None)

    whatsapp_action = MagicMock()
    whatsapp_action.is_meta_provider.return_value = True
    whatsapp_action._env_app_secret.return_value = APP_SECRET
    whatsapp_action.ignore_list = []
    whatsapp_action.utterance_max_length = 10000
    whatsapp_action.media_batch_window = 1.5
    whatsapp_action.should_ignore_flow_nfm_reply = AsyncMock(return_value=False)

    from jvagent.action.whatsapp.modules.meta_api import MetaWhatsAppAPI

    meta_api = MetaWhatsAppAPI(
        api_url="https://graph.facebook.com/v25.0/",
        session="106540352242922",
        token="test-token",
        phone_number_id="106540352242922",
    )
    whatsapp_action.api = AsyncMock(return_value=meta_api)

    with (
        patch(
            "jvagent.action.whatsapp.endpoints._agent_and_whatsapp_action_for_webhook",
            AsyncMock(return_value=(agent, whatsapp_action)),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints.is_directed_message",
            AsyncMock(return_value=True),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints.create_task",
            AsyncMock(side_effect=lambda coro, **kw: (coro.close(), None)[1]),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints._batch_manager.flush_pending_batch_if_stale",
            AsyncMock(return_value=None),
        ),
    ):
        yield agent, whatsapp_action, meta_api


class TestMetaWebhookInteractSmoke:
    @pytest.mark.asyncio
    async def test_first_message_accepted_second_duplicate_ignored(
        self, mock_meta_webhook_stack
    ):
        """Replay same Meta webhook body → duplicate ignored (no second orchestrator run)."""
        _agent, _wa, meta_api = mock_meta_webhook_stack

        async def noop_typing(*args, **kwargs):
            return {"ok": True}

        meta_api.set_typing_status = noop_typing  # type: ignore[method-assign]

        req = _meta_post_request(SAMPLE_TEXT_WEBHOOK)
        req.state.raw_body = json.dumps(SAMPLE_TEXT_WEBHOOK).encode("utf-8")

        first = await whatsapp_interact(req, AGENT_ID)
        assert first == {"status": "received"}

        req2 = _meta_post_request(SAMPLE_TEXT_WEBHOOK)
        req2.state.raw_body = json.dumps(SAMPLE_TEXT_WEBHOOK).encode("utf-8")

        second = await whatsapp_interact(req2, AGENT_ID)
        assert second == {"status": "ignored", "response": "duplicate webhook"}

    @pytest.mark.asyncio
    async def test_status_only_webhook_does_not_block_user_message(
        self, mock_meta_webhook_stack
    ):
        from .test_meta_api import STATUS_ONLY_WEBHOOK

        _agent, _wa, meta_api = mock_meta_webhook_stack
        meta_api.set_typing_status = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]

        status_req = _meta_post_request(STATUS_ONLY_WEBHOOK)
        status_req.state.raw_body = json.dumps(STATUS_ONLY_WEBHOOK).encode("utf-8")
        status_result = await whatsapp_interact(status_req, AGENT_ID)
        assert status_result == {"status": "ignored", "response": "Ignore message"}

        text_req = _meta_post_request(SAMPLE_TEXT_WEBHOOK)
        text_req.state.raw_body = json.dumps(SAMPLE_TEXT_WEBHOOK).encode("utf-8")
        text_result = await whatsapp_interact(text_req, AGENT_ID)
        assert text_result == {"status": "received"}
