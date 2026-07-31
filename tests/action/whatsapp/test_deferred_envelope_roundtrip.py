"""The deferred-interact payload must survive jvspatial's REAL envelope code.

The original smoke test asserted the shape of the ``create_task`` call and
stopped there — which is exactly why the envelope bug shipped: the task dict
carried a top-level ``"payload"`` key, jvspatial's Lambda/local transports
flattened the dict into the invoke envelope (``_build_invoke_body``), and
``normalize_deferred_envelope`` then treated the surviving ``"payload"`` dict as
an SQS-style envelope to rebuild from — silently discarding ``agent_id``,
``sender`` and ``utterance``. The deferred handler 400'd with the wamid already
claimed: total message loss on every non-SQS transport, invisible to a test
that never crossed the repo boundary.

These tests cross it. They capture the dict the endpoint actually sends and
push it through the real jvspatial transforms, asserting the handler-required
fields come out the other side.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.integrations.webhooks.utils import generate_hmac_signature
from jvspatial.serverless.deferred_invoke import normalize_deferred_envelope
from jvspatial.serverless.tasks.aws_lambda import _build_invoke_body
from starlette.requests import Request

from jvagent.action.utils.meta_webhook_dedup import clear_meta_wamid_cache
from jvagent.action.whatsapp.endpoints import whatsapp_interact

from .test_meta_api import SAMPLE_TEXT_WEBHOOK

WAMID = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA="
APP_SECRET = "test-app-secret"
AGENT_ID = "n.Agent.envelope"

# Every field handle_whatsapp_interact_deferred_event refuses to run without.
HANDLER_REQUIRED = ("agent_id", "sender")


def _meta_post_request(body: dict) -> Request:
    raw = json.dumps(body).encode("utf-8")
    sig = generate_hmac_signature(raw, APP_SECRET)
    request = Request(
        {
            "type": "http",
            "asgi": {"spec_version": "2.3", "version": "3.0"},
            "method": "POST",
            "path": f"/api/whatsapp/interact/webhook/{AGENT_ID}",
            "headers": [(b"x-hub-signature-256", sig.encode("ascii"))],
        }
    )
    request.state.raw_body = raw
    return request


@pytest.fixture(autouse=True)
def _clear_dedup():
    clear_meta_wamid_cache()
    yield
    clear_meta_wamid_cache()


async def _capture_deferred_payload() -> dict:
    """Run the real webhook handler in serverless mode; return the task dict it
    schedules. Mirrors the smoke-test harness — only ``create_task`` is
    captured, everything upstream of it is the production code path."""
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
    meta_api.set_typing_status = AsyncMock(return_value={"ok": True})
    whatsapp_action.api = AsyncMock(return_value=meta_api)

    captured: list = []

    async def capture_create_task(task_type, payload=None, **kw):
        # NB: first param must not be called ``name`` — the endpoint also
        # passes a ``name=`` keyword for the task label.
        captured.append(payload)
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
            AsyncMock(side_effect=capture_create_task),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints._batch_manager.flush_pending_batch_if_stale",
            AsyncMock(return_value=None),
        ),
        patch(
            "jvagent.action.whatsapp.endpoints.is_serverless_mode",
            return_value=True,
        ),
    ):
        result = await whatsapp_interact(
            _meta_post_request(SAMPLE_TEXT_WEBHOOK), AGENT_ID
        )

    assert result == {"status": "received"}
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_endpoint_payload_survives_the_lambda_envelope():
    """The dict the endpoint really sends, through the real flatten+normalize."""
    task_payload = await _capture_deferred_payload()

    body = _build_invoke_body("jvagent.whatsapp.interact", task_payload, None)
    normalized = normalize_deferred_envelope(body)

    for field in HANDLER_REQUIRED:
        assert normalized.get(field) == task_payload[field], (
            f"{field!r} was destroyed by the envelope round-trip — the deferred "
            "handler will 400 and the message is lost with its wamid claimed"
        )
    assert normalized.get("utterance") == task_payload["utterance"]
    assert isinstance(normalized.get("wa_message"), dict)
    assert normalized["wa_message"]["message_id"] == WAMID


@pytest.mark.asyncio
async def test_handler_accepts_the_normalized_event():
    """One step further: the normalized event satisfies the handler's own field
    extraction (agent lookup is stubbed to isolate the parsing contract)."""
    from jvagent.action.whatsapp.utils import endpoint_helpers

    task_payload = await _capture_deferred_payload()
    normalized = normalize_deferred_envelope(
        _build_invoke_body("jvagent.whatsapp.interact", task_payload, None)
    )

    processed: list = []

    async def fake_process(data, utterance, sender, agent_id, agent, **kw):
        processed.append((data, utterance, sender, agent_id))

    with (
        patch(
            "jvagent.core.agent.Agent.get",
            AsyncMock(return_value=MagicMock()),
        ),
        patch.object(endpoint_helpers, "_process_interaction_async", fake_process),
    ):
        result = await endpoint_helpers.handle_whatsapp_interact_deferred_event(
            normalized
        )

    assert result["ok"] is True
    assert len(processed) == 1
    data, utterance, sender, agent_id = processed[0]
    assert agent_id == AGENT_ID
    assert data.message_id == WAMID


@pytest.mark.asyncio
async def test_handler_still_accepts_the_legacy_payload_key():
    """Messages already in flight on SQS at deploy time carry the old key —
    the only transport where it ever arrived intact."""
    from jvagent.action.whatsapp.utils import endpoint_helpers

    legacy_event = {
        "task_type": "jvagent.whatsapp.interact",
        "agent_id": AGENT_ID,
        "sender": "16503879439",
        "utterance": "hi",
        "payload": {
            "message_id": WAMID,
            "event_type": "message",
            "message_type": "chat",
            "author": "1",
            "sender": "16503879439",
            "receiver": "2",
            "body": "hi",
        },
    }
    with (
        patch("jvagent.core.agent.Agent.get", AsyncMock(return_value=MagicMock())),
        patch.object(endpoint_helpers, "_process_interaction_async", AsyncMock()),
    ):
        result = await endpoint_helpers.handle_whatsapp_interact_deferred_event(
            legacy_event
        )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_missing_field_rejection_does_not_log_the_event(caplog):
    """The 400 branch used to dump the whole event — message body, phone
    number, quoted base64 — at WARNING. It must log field presence only."""
    import logging

    from jvagent.action.whatsapp.utils import endpoint_helpers
    from fastapi import HTTPException

    secret_body = "my very private message +15551234567"
    event = {"task_type": "jvagent.whatsapp.interact", "body_leak": secret_body}
    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            await endpoint_helpers.handle_whatsapp_interact_deferred_event(event)
    assert exc_info.value.status_code == 400
    assert secret_body not in caplog.text
    assert "MISSING" in caplog.text
