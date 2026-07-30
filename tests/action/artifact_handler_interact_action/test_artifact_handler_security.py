"""Security hardening tests for artifact_handler vault fetch + notify."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (
    _fetch_url_bytes_for_vault,
)
from jvagent.action.artifact_handler_interact_action.endpoints import (
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
        assert resp.status_code == 404
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
    ):
        out = await artifact_handler_notify(req, "Agent:a")
        assert out["status"] == "already_imported"
        import_graph.assert_not_awaited()
