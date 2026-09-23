"""Security hardening tests for artifact_handler vault fetch + notify."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (
    ArtifactHandlerInteractAction,
    _fetch_url_bytes_for_vault,
)
from jvagent.action.artifact_handler_interact_action.endpoints import (
    _is_trusted_notify_artifact_url,
    _scan_sibling_actions_for_job,
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
async def test_notify_webhook_url_has_no_api_key(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "http://localhost:8000")
    action = ArtifactHandlerInteractAction()
    with (
        patch.object(
            ArtifactHandlerInteractAction,
            "get_agent",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="n.Agent.test123", name="TestAgent"),
        ),
        patch.object(ArtifactHandlerInteractAction, "save", new_callable=AsyncMock),
        patch(
            "jvspatial.api.auth.api_key_service.APIKeyService",
        ) as key_svc,
    ):
        url = await action.get_notify_webhook_url()
    assert (
        url
        == "http://localhost:8000/api/artifact_handler_action/notify/n.Agent.test123"
    )
    assert "api_key" not in url
    key_svc.assert_not_called()


@pytest.mark.asyncio
async def test_notify_webhook_strips_legacy_api_key_query(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "http://localhost:8000")
    action = ArtifactHandlerInteractAction()
    action.notify_webhook_url = (
        "http://localhost:8000/api/artifact_handler_action/notify/"
        "n.Agent.test123?api_key=old"
    )
    with (
        patch.object(
            ArtifactHandlerInteractAction,
            "get_agent",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="n.Agent.test123", name="TestAgent"),
        ),
        patch.object(
            ArtifactHandlerInteractAction, "save", new_callable=AsyncMock
        ) as save,
    ):
        url = await action.get_notify_webhook_url()
    assert url == (
        "http://localhost:8000/api/artifact_handler_action/notify/n.Agent.test123"
    )
    save.assert_awaited()


@pytest.mark.asyncio
async def test_notify_webhook_reuses_matching_public_url(monkeypatch):
    monkeypatch.setenv("JVAGENT_PUBLIC_BASE_URL", "http://localhost:8000")
    existing = (
        "http://localhost:8000/api/artifact_handler_action/notify/n.Agent.test123"
    )
    action = ArtifactHandlerInteractAction()
    action.notify_webhook_url = existing
    with (
        patch.object(
            ArtifactHandlerInteractAction,
            "get_agent",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="n.Agent.test123", name="TestAgent"),
        ),
        patch.object(
            ArtifactHandlerInteractAction, "save", new_callable=AsyncMock
        ) as save,
    ):
        url = await action.get_notify_webhook_url()
    assert url == existing
    save.assert_not_awaited()


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


def _request(*, payload: dict | None = None):
    req = MagicMock()
    req.state = SimpleNamespace(user=None)
    req.json = AsyncMock(return_value=payload or {})
    return req


def _trusted_artifact():
    return patch(
        "jvagent.action.artifact_handler_interact_action.endpoints._is_trusted_notify_artifact_url",
        return_value=True,
    )


async def _inline_create_task(coro_or_type, payload=None, **kwargs):
    """Lambda Shape B: await the coroutine and return None."""
    if asyncio.iscoroutine(coro_or_type):
        await coro_or_type
        return None
    raise AssertionError(f"expected a coroutine, got {type(coro_or_type)!r}")


@pytest.mark.asyncio
async def test_notify_rejects_missing_job_id(caplog):
    req = _request(payload={"process_document_url": "https://example.com/a"})
    with caplog.at_level(logging.WARNING):
        with patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
        ) as resolve:
            resp = await artifact_handler_notify(req, "Agent:a")
            assert resp.status_code == 400
            resolve.assert_not_awaited()
    assert "missing job_id" in caplog.text


@pytest.mark.asyncio
async def test_notify_rejects_missing_process_document_url(caplog):
    req = _request(payload={"job_id": "job-1"})
    with caplog.at_level(logging.WARNING):
        resp = await artifact_handler_notify(req, "Agent:a")
    assert resp.status_code == 400
    assert "missing process_document_url" in caplog.text


@pytest.mark.asyncio
async def test_notify_rejects_untrusted_artifact_url():
    action = SimpleNamespace(
        lookup_job=AsyncMock(
            return_value={"job_id": "job-1", "agent_id": "Agent:a", "notified": False}
        ),
        jvforge_job_index={},
    )
    req = _request(
        payload={
            "process_document_url": "https://evil.example/secret.json",
            "job_id": "job-1",
        },
    )
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=action,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
        ) as import_graph,
    ):
        resp = await artifact_handler_notify(req, "Agent:a")
        assert resp.status_code == 403
        import_graph.assert_not_awaited()


def test_notify_rejects_artifact_url_for_other_job():
    assert not _is_trusted_notify_artifact_url(
        "https://jvforge.example/v1/artifacts/other-job", "job-1"
    )


def test_notify_rejects_artifact_when_jvforge_base_unset(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: None,
    )
    assert not _is_trusted_notify_artifact_url(
        "https://jvforge.example/v1/artifacts/job-1", "job-1"
    )


def test_notify_trusts_matching_artifact_on_jvforge_base(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://jvforge.example",
    )
    assert _is_trusted_notify_artifact_url(
        "https://jvforge.example/v1/artifacts/job-1", "job-1"
    )


@pytest.mark.asyncio
async def test_notify_imports_when_job_and_trusted_artifact(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://jvforge.example",
    )
    action = SimpleNamespace(
        lookup_job=AsyncMock(
            return_value={
                "job_id": "job-1",
                "agent_id": "Agent:a",
                "notified": False,
                "channel": "default",
            }
        ),
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
        jvforge_job_index={"job-1": {}},
    )
    req = _request(
        payload={
            "process_document_url": "https://jvforge.example/v1/artifacts/job-1",
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
            return_value="Doc.md",
        ) as import_graph,
    ):
        out = await artifact_handler_notify(req, "Agent:a")
    assert out["status"] == "imported"
    import_graph.assert_awaited_once()
    action.mark_notified.assert_awaited_once_with("job-1")
    action.clear_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_notify_imports_when_raw_record_has_job_and_cache_empty(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://jvforge.example",
    )
    raw_entry = {
        "job_id": "job-1",
        "agent_id": "Agent:a",
        "notified": False,
        "channel": "default",
    }
    cached = SimpleNamespace(
        id="action-1",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
    )
    records = [
        {
            "id": "action-1",
            "context": {"jvforge_job_index": {"job-1": raw_entry}},
        }
    ]
    req = _request(
        payload={
            "process_document_url": "https://jvforge.example/v1/artifacts/job-1",
            "job_id": "job-1",
        }
    )

    async def _load(_record):
        return cached

    async def _reload(action):
        return action

    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=cached,
        ),
        patch(
            "jvagent.action.identity.find_records_by_archetype",
            new_callable=AsyncMock,
            return_value=records,
        ),
        patch(
            "jvagent.action.identity.load_action_from_record",
            side_effect=_load,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._reload_action",
            side_effect=_reload,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._evict_action_cache",
            new_callable=AsyncMock,
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
            return_value="Doc.md",
        ) as import_graph,
    ):
        out = await artifact_handler_notify(req, "Agent:a")
    assert out["status"] == "imported"
    import_graph.assert_awaited_once()
    cached.mark_notified.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_notify_skips_import_for_unknown_job(caplog, monkeypatch):
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "dynamodb")
    action = SimpleNamespace(
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
    )
    req = _request(
        payload={
            "process_document_url": "https://example.com/a",
            "job_id": "job-missing",
        }
    )
    with caplog.at_level(logging.WARNING):
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
                "jvagent.action.artifact_handler_interact_action.endpoints._scan_sibling_actions_for_job",
                new_callable=AsyncMock,
                return_value=(None, None),
            ),
        ):
            resp = await artifact_handler_notify(req, "Agent:a")
            assert resp.status_code == 503
            assert resp.headers.get("Retry-After")
            import_graph.assert_not_awaited()
    assert "db_type=dynamodb" in caplog.text


@pytest.mark.asyncio
async def test_notify_finds_job_on_sibling_action(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://jvforge.example",
    )
    empty = SimpleNamespace(
        id="action-empty",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
    )
    holder = SimpleNamespace(
        id="action-holder",
        lookup_job=AsyncMock(
            return_value={
                "job_id": "job-1",
                "agent_id": "Agent:a",
                "notified": False,
                "channel": "default",
            }
        ),
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
        jvforge_job_index={"job-1": {}},
    )
    req = _request(
        payload={
            "process_document_url": "https://jvforge.example/v1/artifacts/job-1",
            "job_id": "job-1",
        }
    )
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=empty,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._scan_sibling_actions_for_job",
            new_callable=AsyncMock,
            return_value=(
                holder,
                {
                    "job_id": "job-1",
                    "agent_id": "Agent:a",
                    "notified": False,
                    "channel": "default",
                },
            ),
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
            return_value="Doc.md",
        ) as import_graph,
    ):
        out = await artifact_handler_notify(req, "Agent:a")
    assert out["status"] == "imported"
    import_graph.assert_awaited_once()
    holder.mark_notified.assert_awaited_once_with("job-1")
    holder.clear_job.assert_awaited_once_with("job-1")
    empty.mark_notified.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_sibling_actions_finds_job_on_second_node():
    empty = SimpleNamespace(
        id="action-empty",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
    )
    holder = SimpleNamespace(
        id="action-holder",
        lookup_job=AsyncMock(return_value={"job_id": "job-1", "agent_id": "Agent:a"}),
        jvforge_job_index={"job-1": {"job_id": "job-1"}},
    )
    records = [{"id": "action-empty"}, {"id": "action-holder"}]
    by_id = {"action-empty": empty, "action-holder": holder}

    async def _load(record):
        return by_id[record["id"]]

    async def _reload(action):
        return action

    with (
        patch(
            "jvagent.action.identity.find_records_by_archetype",
            new_callable=AsyncMock,
            return_value=records,
        ),
        patch(
            "jvagent.action.identity.load_action_from_record",
            side_effect=_load,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._reload_action",
            side_effect=_reload,
        ),
    ):
        found, entry = await _scan_sibling_actions_for_job("Agent:a", "job-1")
    assert found is holder
    assert entry["job_id"] == "job-1"
    empty.lookup_job.assert_awaited_once_with("job-1")
    holder.lookup_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_scan_sibling_load_failure_logs_missing(caplog):
    records = [{"id": "action-bad"}]

    async def _load(_record):
        raise RuntimeError("load boom")

    with caplog.at_level(logging.WARNING):
        with (
            patch(
                "jvagent.action.identity.find_records_by_archetype",
                new_callable=AsyncMock,
                return_value=records,
            ),
            patch(
                "jvagent.action.identity.load_action_from_record",
                side_effect=_load,
            ),
        ):
            found, entry = await _scan_sibling_actions_for_job(
                "Agent:a",
                "job-1",
                skip_id="action-empty",
                skip_index_size=0,
            )
    assert found is None
    assert entry is None
    assert "load action failed" in caplog.text
    assert "action-bad:raw=0:loaded=missing" in caplog.text


@pytest.mark.asyncio
async def test_scan_finds_job_in_raw_record_when_loaded_index_empty():
    cached = SimpleNamespace(
        id="action-1",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
    )
    raw_entry = {"job_id": "job-1", "agent_id": "Agent:a", "notified": False}
    records = [
        {
            "id": "action-1",
            "context": {"jvforge_job_index": {"job-1": raw_entry}},
        }
    ]

    async def _load(_record):
        return cached

    async def _reload(action):
        return action

    with (
        patch(
            "jvagent.action.identity.find_records_by_archetype",
            new_callable=AsyncMock,
            return_value=records,
        ),
        patch(
            "jvagent.action.identity.load_action_from_record",
            side_effect=_load,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._reload_action",
            side_effect=_reload,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._evict_action_cache",
            new_callable=AsyncMock,
        ) as evict,
    ):
        found, entry = await _scan_sibling_actions_for_job(
            "Agent:a", "job-1", skip_id="action-1", skip_index_size=0
        )
    assert found is cached
    assert entry["job_id"] == "job-1"
    assert "job-1" in cached.jvforge_job_index
    evict.assert_awaited()


@pytest.mark.asyncio
async def test_scan_unknown_when_raw_maps_empty():
    cached = SimpleNamespace(
        id="action-1",
        lookup_job=AsyncMock(return_value=None),
        jvforge_job_index={},
    )
    records = [{"id": "action-1", "context": {"jvforge_job_index": {}}}]

    async def _load(_record):
        return cached

    async def _reload(action):
        return action

    with (
        patch(
            "jvagent.action.identity.find_records_by_archetype",
            new_callable=AsyncMock,
            return_value=records,
        ),
        patch(
            "jvagent.action.identity.load_action_from_record",
            side_effect=_load,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._reload_action",
            side_effect=_reload,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._evict_action_cache",
            new_callable=AsyncMock,
        ),
    ):
        found, entry = await _scan_sibling_actions_for_job("Agent:a", "job-1")
    assert found is None
    assert entry is None


@pytest.mark.asyncio
async def test_notify_finds_job_when_resolve_action_missing(monkeypatch):
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://jvforge.example",
    )
    holder = SimpleNamespace(
        id="action-holder",
        lookup_job=AsyncMock(
            return_value={
                "job_id": "job-1",
                "agent_id": "Agent:a",
                "notified": False,
                "channel": "default",
            }
        ),
        mark_notified=AsyncMock(),
        clear_job=AsyncMock(),
        jvforge_job_index={"job-1": {}},
    )
    req = _request(
        payload={
            "process_document_url": "https://jvforge.example/v1/artifacts/job-1",
            "job_id": "job-1",
        }
    )
    with (
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._resolve_action",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._scan_sibling_actions_for_job",
            new_callable=AsyncMock,
            return_value=(
                holder,
                {
                    "job_id": "job-1",
                    "agent_id": "Agent:a",
                    "notified": False,
                    "channel": "default",
                },
            ),
        ),
        patch(
            "jvagent.core.agent.Agent.get",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(id="Agent:a"),
        ),
        patch(
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
            return_value="Doc.md",
        ) as import_graph,
    ):
        out = await artifact_handler_notify(req, "Agent:a")
    assert out["status"] == "imported"
    import_graph.assert_awaited_once()
    holder.mark_notified.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_notify_rejects_job_for_other_agent():
    action = SimpleNamespace(
        lookup_job=AsyncMock(
            return_value={
                "job_id": "job-1",
                "agent_id": "Agent:b",
                "notified": False,
            }
        ),
        jvforge_job_index={},
    )
    req = _request(
        payload={
            "process_document_url": "https://jvforge.example/v1/artifacts/job-1",
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
            "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
            new_callable=AsyncMock,
        ) as import_graph,
    ):
        resp = await artifact_handler_notify(req, "Agent:a")
        assert resp.status_code == 403
        import_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_idempotent_when_already_notified(caplog):
    action = SimpleNamespace(
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
        with caplog.at_level(logging.WARNING):
            out = await artifact_handler_notify(req, "Agent:a")
        assert out["status"] == "already_imported"
        import_graph.assert_not_awaited()
        send.assert_not_awaited()
    assert "already_imported" in caplog.text


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
        _trusted_artifact(),
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
        _trusted_artifact(),
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
