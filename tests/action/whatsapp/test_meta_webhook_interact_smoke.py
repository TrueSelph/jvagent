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

    async def _fake_create_task(coro_or_name, payload=None, **kw):
        # Shape B passes a coroutine; Shape A passes a task-type string.
        if hasattr(coro_or_name, "close"):
            coro_or_name.close()
        return None

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
            AsyncMock(side_effect=_fake_create_task),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints._batch_manager.flush_pending_batch_if_stale",
            AsyncMock(return_value=None),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints.is_serverless_mode",
            return_value=False,
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

    @pytest.mark.asyncio
    async def test_serverless_schedules_deferred_interact_shape_a(
        self, mock_meta_webhook_stack
    ):
        """Lambda path: Shape A create_task + immediate 200 (no inline await)."""
        _agent, _wa, meta_api = mock_meta_webhook_stack
        meta_api.set_typing_status = AsyncMock(return_value={"ok": True})

        create_calls = []

        async def capture_create_task(coro_or_name, payload=None, **kw):
            create_calls.append((coro_or_name, payload, kw))
            if hasattr(coro_or_name, "close"):
                coro_or_name.close()
            return None

        with (
            patch(
                "jvagent.action.whatsapp.endpoints.is_serverless_mode",
                return_value=True,
            ),
            patch(
                "jvagent.action.whatsapp.endpoints.create_task",
                AsyncMock(side_effect=capture_create_task),
            ),
        ):
            req = _meta_post_request(SAMPLE_TEXT_WEBHOOK)
            req.state.raw_body = json.dumps(SAMPLE_TEXT_WEBHOOK).encode("utf-8")
            result = await whatsapp_interact(req, AGENT_ID)

        assert result == {"status": "received"}
        assert len(create_calls) == 1
        name, payload, kw = create_calls[0]
        assert name == "jvagent.whatsapp.interact"
        assert payload["agent_id"] == AGENT_ID
        assert payload["payload"]["message_id"] == WAMID
        assert kw.get("strict") is True

    @pytest.mark.asyncio
    async def test_serverless_schedule_failure_forgets_wamid(
        self, mock_meta_webhook_stack
    ):
        """Failed Shape A schedule must not leave wamid claimed (silent drop)."""
        from fastapi import HTTPException

        from jvagent.action.utils.meta_webhook_dedup import remember_meta_wamid

        _agent, _wa, meta_api = mock_meta_webhook_stack
        meta_api.set_typing_status = AsyncMock(return_value={"ok": True})

        with (
            patch(
                "jvagent.action.whatsapp.endpoints.is_serverless_mode",
                return_value=True,
            ),
            patch(
                "jvagent.action.whatsapp.endpoints.create_task",
                AsyncMock(side_effect=RuntimeError("noop scheduler")),
            ),
        ):
            req = _meta_post_request(SAMPLE_TEXT_WEBHOOK)
            req.state.raw_body = json.dumps(SAMPLE_TEXT_WEBHOOK).encode("utf-8")
            with pytest.raises(HTTPException) as exc_info:
                await whatsapp_interact(req, AGENT_ID)
            assert exc_info.value.status_code == 503

        # wamid must be free for Meta/jvconnect retry
        assert remember_meta_wamid(WAMID) is True


def test_message_payload_from_dict_roundtrip():
    from jvagent.action.whatsapp.modules.base import MessagePayload
    from jvagent.action.whatsapp.utils.endpoint_helpers import (
        _convert_message_payload_to_dict,
        message_payload_from_dict,
    )

    original = MessagePayload(
        message_id="wamid.x",
        event_type="message",
        message_type="chat",
        author="1",
        sender="1",
        receiver="2",
        body="hello",
        sender_name="Ada",
    )
    rebuilt = message_payload_from_dict(_convert_message_payload_to_dict(original))
    assert rebuilt.message_id == "wamid.x"
    assert rebuilt.body == "hello"
    assert rebuilt.sender_name == "Ada"
