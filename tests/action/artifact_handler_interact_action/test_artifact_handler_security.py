"""Security hardening tests for artifact_handler vault fetch + notify."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (
    _fetch_url_bytes_for_vault,
)
from jvagent.action.artifact_handler_interact_action.endpoints import (
    _send_whatsapp_notifications,
    artifact_handler_notify,
)
from jvagent.action.artifact_handler_interact_action.webhook_auth import (
    notify_endpoint_for_agent,
)


def test_notify_endpoint_for_agent_is_exact_path():
    path = notify_endpoint_for_agent("Agent:abc")
    assert path == "/api/artifact_handler_action/notify/Agent:abc"
    assert not path.endswith("*")


@pytest.mark.asyncio
async def test_fetch_url_bytes_for_vault_uses_ssrf_guard():
    with patch(
        "jvagent.action.pageindex.url_guard.fetch_url_bytes_capped",
        new_callable=AsyncMock,
    ) as fetch:
        fetch.side_effect = ValidationError("URL resolves to a non-public address")
        out = await _fetch_url_bytes_for_vault("http://127.0.0.1/secret")
        assert out is None
        fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_url_bytes_for_vault_returns_bytes_on_ok():
    with patch(
        "jvagent.action.pageindex.url_guard.fetch_url_bytes_capped",
        new_callable=AsyncMock,
        return_value=(b"%PDF-1.4", "doc.pdf", "application/pdf"),
    ) as fetch:
        out = await _fetch_url_bytes_for_vault("https://cdn.example.com/a.pdf")
        assert out == b"%PDF-1.4"
        fetch.assert_awaited_once()


def _request(*, api_key_id: str = "key-1", payload: dict | None = None):
    req = MagicMock()
    req.state = SimpleNamespace(user={"api_key_id": api_key_id, "user_id": "sys"})
    req.json = AsyncMock(return_value=payload or {})
    return req


async def _inline_create_task(coro_or_type, payload=None, **kwargs):
    """Lambda Shape B: await the coroutine and return None."""
    if asyncio.iscoroutine(coro_or_type):
        await coro_or_type
        return None
    raise AssertionError(f"expected a coroutine, got {type(coro_or_type)!r}")


@pytest.mark.asyncio
async def test_notify_rejects_missing_job_id():
    req = _request(payload={"process_document_url": "https://example.com/a"})
    with patch(
        "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
        new_callable=AsyncMock,
    ) as resolve:
        resp = await artifact_handler_notify(req, "Agent:a")
        assert resp.status_code == 400
        resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_rejects_mismatched_api_key():
    action = SimpleNamespace(
        notify_webhook_api_key_id="key-expected",
        lookup_job=AsyncMock(),
    )
    req = _request(
        api_key_id="key-other",
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-1",
        },
    )
    with patch(
        "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
        new_callable=AsyncMock,
        return_value=action,
    ):
        resp = await artifact_handler_notify(req, "Agent:a")
        assert resp.status_code == 403
        action.lookup_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_skips_import_for_unknown_job():
    action = SimpleNamespace(
        notify_webhook_api_key_id="key-1",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
    )
    req = _request(
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-missing",
        }
    )
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
        ) as import_graph,
    ):
        resp = await artifact_handler_notify(req, "Agent:a")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After")
        import_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_idempotent_when_already_notified():
    action = SimpleNamespace(
        notify_webhook_api_key_id="key-1",
        lookup_job=AsyncMock(
            return_value={
                "job_id": "job-1",
                "agent_id": "Agent:a",
                "notified": True,
                "doc_name": "Doc.md",
            }
        ),
    )
    req = _request(
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-1",
        }
    )
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
        ) as import_graph,
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._publish_whatsapp_message",
            new_callable=AsyncMock,
        ) as send,
    ):
        out = await artifact_handler_notify(req, "Agent:a")
        assert out["status"] == "already_imported"
        import_graph.assert_not_awaited()
        send.assert_not_awaited()


def _whatsapp_job(**extra):
    entry = {
        "job_id": "job-1",
        "agent_id": "Agent:a",
        "notified": False,
        "user_id": "5926431530",
        "session_id": "sess-1",
        "conversation_id": "",
        "channel": "whatsapp",
        "doc_name": "upload.jpg",
        "filename": "upload.jpg",
    }
    entry.update(extra)
    return entry


def _notify_action(job_entry):
    return SimpleNamespace(
        notify_webhook_api_key_id="key-1",
        lookup_job=AsyncMock(return_value=job_entry),
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
        jvforge_job_index={"job-1": job_entry},
    )


@pytest.mark.asyncio
async def test_notify_awaits_whatsapp_send_before_clearing_job():
    action = _notify_action(_whatsapp_job())
    req = _request(
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-1",
            "doc_name": "upload.jpg",
        }
    )
    send = AsyncMock(return_value=True)
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
            return_value="5926431530_upload.jpg",
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._generate_ready_content",
            new_callable=AsyncMock,
            return_value=("Your image is ready. Ask me anything about it.", False),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._publish_whatsapp_message",
            send,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints.create_task",
            _inline_create_task,
        ),
    ):
        out = await artifact_handler_notify(req, "Agent:a")
    assert out["status"] == "imported"
    assert out["notified"] is True
    send.assert_awaited_once()
    action.mark_notified.assert_awaited_once_with("job-1")
    action.clear_job.assert_awaited_once_with("job-1")
    assert send.await_args.kwargs["user_id"] == "5926431530"
    assert send.await_args.kwargs["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_notify_returns_503_when_whatsapp_send_fails():
    action = _notify_action(_whatsapp_job())
    req = _request(
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-1",
        }
    )
    send = AsyncMock(return_value=False)
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
            return_value="5926431530_upload.jpg",
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._generate_ready_content",
            new_callable=AsyncMock,
            return_value=("Your image is ready. Ask me anything about it.", False),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._publish_whatsapp_message",
            send,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints.create_task",
            _inline_create_task,
        ),
    ):
        resp = await artifact_handler_notify(req, "Agent:a")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")
    send.assert_awaited_once()
    action.mark_notified.assert_not_awaited()
    action.clear_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_whatsapp_uses_canned_when_generate_times_out():
    action = SimpleNamespace(id="action-1")
    agent = SimpleNamespace(id="Agent:a")

    async def _hang(**_kwargs):
        await asyncio.sleep(30)
        return "should not be used"

    publish = AsyncMock(return_value=True)
    with (
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=agent,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._doc_description_lookup",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._generate_ready_message",
            _hang,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._READY_GENERATE_TIMEOUT_S",
            0.05,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._publish_whatsapp_message",
            publish,
        ),
    ):
        ok = await _send_whatsapp_notifications(
            agent_id="Agent:a",
            job_id="job-1",
            user_id="5926431530",
            session_id="sess-1",
            conversation_id="conv-1",
            internal_doc_name="upload.jpg",
            display_doc="upload.jpg",
            pending_question="what is this?",
        )
    assert ok is True
    publish.assert_awaited_once()
    assert publish.await_args.kwargs["answered"] is False
    content = publish.await_args.kwargs["content"] or ""
    assert "ready" in content.lower()
    assert "what is this?" in content
