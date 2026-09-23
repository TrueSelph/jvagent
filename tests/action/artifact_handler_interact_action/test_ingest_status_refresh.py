"""Reverse-index persistence, vault status helper, and jvforge pull-import refresh."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (
    ArtifactHandlerInteractAction,
)
from jvagent.action.artifact_handler_interact_action.job_status import (
    apply_ingest_job_status,
)

_CUSTOM_TOOLS_PATH = (
    Path(__file__).resolve().parents[3]
    / "jvagent"
    / "skills"
    / "artifact_handler"
    / "scripts"
    / "custom_tools.py"
)


def _load_custom_tools():
    spec = importlib.util.spec_from_file_location(
        "artifact_handler_custom_tools_refresh_test", _CUSTOM_TOOLS_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeConversation:
    def __init__(self, vault):
        self.context = {"artifact_handler": vault}

    async def update_context(self, data):
        for key, value in data.items():
            if key == "artifact_handler" and isinstance(value, dict):
                vault = dict(self.context.get("artifact_handler") or {})
                vault.update(value)
                self.context["artifact_handler"] = vault
            else:
                self.context[key] = value


def _action(*, save=None):
    action = SimpleNamespace(
        jvforge_job_index={},
        agent_id="n.Agent.a",
        save=save or AsyncMock(),
    )
    action._persist_job_index = (
        ArtifactHandlerInteractAction._persist_job_index.__get__(action)
    )
    action.register_job = ArtifactHandlerInteractAction.register_job.__get__(action)
    action.submit_ingest = ArtifactHandlerInteractAction.submit_ingest.__get__(action)
    return action


@pytest.mark.asyncio
async def test_register_job_save_failure_raises():
    action = _action(save=AsyncMock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError, match="db down"):
        await action.register_job(
            job_id="job-1",
            user_id="u1",
            conversation_id="c1",
            session_id="s1",
            channel="whatsapp",
            doc_name="doc.jpg",
            agent_id="n.Agent.a",
        )


@pytest.mark.asyncio
async def test_register_job_empty_job_id_logs(caplog):
    action = _action()
    action.id = "n.Action.vault1"
    with caplog.at_level(logging.WARNING):
        await action.register_job(
            job_id="",
            user_id="u1",
            conversation_id="c1",
            session_id="s1",
            channel="whatsapp",
            doc_name="doc.jpg",
            agent_id="n.Agent.a",
        )
    assert "register_job skipped empty job_id" in caplog.text
    assert action.jvforge_job_index == {}


@pytest.mark.asyncio
async def test_register_job_roundtrip_reload_ok():
    action = _action()
    action.id = "n.Action.vault1"
    fresh = SimpleNamespace(jvforge_job_index={"job-1": {"job_id": "job-1"}})
    with patch(
        "jvagent.action.base.Action.get",
        new_callable=AsyncMock,
        return_value=fresh,
    ):
        await action.register_job(
            job_id="job-1",
            user_id="u1",
            conversation_id="c1",
            session_id="s1",
            channel="whatsapp",
            doc_name="doc.jpg",
            agent_id="n.Agent.a",
        )
    assert "job-1" in action.jvforge_job_index


@pytest.mark.asyncio
async def test_register_job_evicts_cache_before_reload(caplog):
    action = _action()
    action.id = "n.Action.vault1"
    evict = AsyncMock()
    ctx = SimpleNamespace(_evict_from_cache=evict)
    fresh = SimpleNamespace(jvforge_job_index={"job-1": {"job_id": "job-1"}})
    with (
        patch(
            "jvspatial.core.context.get_default_context",
            return_value=ctx,
        ),
        patch(
            "jvagent.action.base.Action.get",
            new_callable=AsyncMock,
            return_value=fresh,
        ) as get_action,
    ):
        with caplog.at_level(logging.WARNING):
            await action.register_job(
                job_id="job-1",
                user_id="u1",
                conversation_id="c1",
                session_id="s1",
                channel="whatsapp",
                doc_name="doc.jpg",
                agent_id="n.Agent.a",
            )
    evict.assert_awaited_once_with("n.Action.vault1")
    assert get_action.await_count >= 1
    assert get_action.await_args_list[0][0][0] == "n.Action.vault1"
    assert "cache evict action_id=n.Action.vault1" in caplog.text


@pytest.mark.asyncio
async def test_register_job_roundtrip_failed_raises():
    action = _action()
    action.id = "n.Action.vault1"
    with patch(
        "jvagent.action.base.Action.get",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(jvforge_job_index={}),
    ):
        with pytest.raises(RuntimeError, match="did not persist"):
            await action.register_job(
                job_id="job-1",
                user_id="u1",
                conversation_id="c1",
                session_id="s1",
                channel="whatsapp",
                doc_name="doc.jpg",
                agent_id="n.Agent.a",
            )


@pytest.mark.asyncio
async def test_submit_ingest_fails_when_register_job_cannot_save(monkeypatch):
    action = _action(save=AsyncMock(side_effect=RuntimeError("db down")))
    page_index = SimpleNamespace(
        get_webhook_url=AsyncMock(return_value="https://example/llm")
    )
    action.get_action = AsyncMock(return_value=page_index)
    action.get_notify_webhook_url = AsyncMock(return_value="https://example/notify")

    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://forge.example",
    )

    async def fake_assimilate(**_kwargs):
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(
        "jvagent.action.pageindex.jvforge_assimilate.assimilate_via_jvforge_async",
        fake_assimilate,
    )
    with pytest.raises(RuntimeError, match="db down"):
        await action.submit_ingest(
            doc="https://files.example/a.jpg",
            doc_name="a.jpg",
            user_id="u1",
            conversation_id="c1",
            session_id="s1",
            channel="whatsapp",
        )


@pytest.mark.asyncio
async def test_submit_ingest_registers_nested_job_id(monkeypatch, caplog):
    action = _action()
    action.id = "n.Action.vault1"
    page_index = SimpleNamespace(
        get_webhook_url=AsyncMock(return_value="https://example/llm")
    )
    action.get_action = AsyncMock(return_value=page_index)
    action.get_notify_webhook_url = AsyncMock(return_value="https://example/notify")
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://forge.example",
    )

    async def fake_assimilate(**_kwargs):
        return {"job_id": "nested-job", "status": "queued"}

    monkeypatch.setattr(
        "jvagent.action.pageindex.jvforge_assimilate.assimilate_via_jvforge_async",
        fake_assimilate,
    )
    fresh = SimpleNamespace(jvforge_job_index={"nested-job": {"job_id": "nested-job"}})
    with patch(
        "jvagent.action.base.Action.get",
        new_callable=AsyncMock,
        return_value=fresh,
    ):
        with caplog.at_level(logging.WARNING):
            result = await action.submit_ingest(
                doc="https://files.example/a.jpg",
                doc_name="a.jpg",
                user_id="u1",
                conversation_id="c1",
                session_id="s1",
                channel="whatsapp",
            )
    assert result["job_id"] == "nested-job"
    assert "nested-job" in action.jvforge_job_index
    assert "submit_ingest queued job_id=nested-job" in caplog.text


@pytest.mark.asyncio
async def test_submit_ingest_skip_register_when_job_id_empty(monkeypatch, caplog):
    action = _action()
    action.id = "n.Action.vault1"
    page_index = SimpleNamespace(
        get_webhook_url=AsyncMock(return_value="https://example/llm")
    )
    action.get_action = AsyncMock(return_value=page_index)
    action.get_notify_webhook_url = AsyncMock(return_value="https://example/notify")
    monkeypatch.setattr(
        "jvagent.env.get_jvagent_jvforge_base_url",
        lambda: "https://forge.example",
    )

    async def fake_assimilate(**_kwargs):
        return {"status": "queued", "job_id": ""}

    monkeypatch.setattr(
        "jvagent.action.pageindex.jvforge_assimilate.assimilate_via_jvforge_async",
        fake_assimilate,
    )
    with caplog.at_level(logging.WARNING):
        result = await action.submit_ingest(
            doc="https://files.example/a.jpg",
            doc_name="a.jpg",
            user_id="u1",
            conversation_id="c1",
            session_id="s1",
            channel="whatsapp",
        )
    assert result.get("job_id") == ""
    assert action.jvforge_job_index == {}
    assert "submit_ingest queued job_id=-" in caplog.text
    assert "submit_ingest skip register_job" in caplog.text


@pytest.mark.asyncio
async def test_apply_ingest_job_status_updates_pending_and_vault():
    conv = FakeConversation(
        {
            "pending_ingest_jobs": {
                "job-1": {"doc_name": "user_a.jpg", "status": "queued"}
            },
            "private_u1": [
                {
                    "doc_name": "user_a.jpg",
                    "job_id": "job-1",
                    "status": "queued",
                    "filename": "a.jpg",
                }
            ],
        }
    )
    ok = await apply_ingest_job_status(conv, "job-1", "ready", doc_name="user_a.jpg")
    assert ok is True
    vault = conv.context["artifact_handler"]
    assert vault["pending_ingest_jobs"]["job-1"]["status"] == "ready"
    assert vault["private_u1"][0]["status"] == "ready"
    assert vault["active_doc_name"] == "user_a.jpg"


@pytest.mark.asyncio
async def test_refresh_pageindex_hit_marks_ready(monkeypatch):
    ct = _load_custom_tools()
    conv = FakeConversation(
        {
            "pending_ingest_jobs": {
                "job-1": {"doc_name": "user_a.jpg", "status": "queued"}
            },
            "private_u1": [
                {"doc_name": "user_a.jpg", "job_id": "job-1", "status": "queued"}
            ],
        }
    )
    page_index = SimpleNamespace(
        list_documents=AsyncMock(return_value=[{"doc_name": "user_a.jpg"}])
    )
    interview = SimpleNamespace(get_action=AsyncMock(return_value=page_index))
    ctx = SimpleNamespace(
        interview=interview,
        visitor=SimpleNamespace(user_id="u1"),
        add_directive=lambda _msg: None,
    )

    result = await ct._maybe_refresh_pending_jobs(
        ctx, conv, say_ready=False, session_id="s1", user_id="u1"
    )
    assert result["became_ready"] == ["user_a.jpg"]
    assert result["still_queued"] == []
    assert (
        conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]["status"]
        == "ready"
    )


@pytest.mark.asyncio
async def test_refresh_webhook_failed_pull_imports(monkeypatch):
    ct = _load_custom_tools()
    conv = FakeConversation(
        {
            "pending_ingest_jobs": {
                "job-1": {"doc_name": "user_a.jpg", "status": "queued"}
            },
            "private_u1": [
                {"doc_name": "user_a.jpg", "job_id": "job-1", "status": "queued"}
            ],
        }
    )
    page_index = SimpleNamespace(list_documents=AsyncMock(return_value=[]))
    dv_action = SimpleNamespace(
        agent_id="n.Agent.a",
        get_job_status=AsyncMock(
            return_value={
                "status": "webhook_failed",
                "artifact_url": "https://forge.example/v1/artifacts/job-1",
            }
        ),
        confirm_artifact_imported=AsyncMock(),
    )

    async def fake_get_action(name):
        if name == "PageIndexAction":
            return page_index
        if name == "ArtifactHandlerInteractAction":
            return dv_action
        return None

    interview = SimpleNamespace(get_action=fake_get_action)
    ctx = SimpleNamespace(
        interview=interview,
        visitor=SimpleNamespace(user_id="u1"),
        add_directive=lambda _msg: None,
    )

    async def fake_import(url, agent_id):
        assert "artifacts/job-1" in url
        assert agent_id == "n.Agent.a"
        return "user_a.jpg"

    monkeypatch.setattr(
        "jvagent.action.artifact_handler_interact_action.endpoints._download_and_import_graph",
        fake_import,
    )

    result = await ct._maybe_refresh_pending_jobs(
        ctx, conv, say_ready=False, session_id="s1", user_id="u1"
    )
    assert result["became_ready"] == ["user_a.jpg"]
    assert result["still_queued"] == []
    assert (
        conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]["status"]
        == "ready"
    )
    dv_action.confirm_artifact_imported.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_refresh_jvforge_failed_marks_failed():
    ct = _load_custom_tools()
    conv = FakeConversation(
        {
            "pending_ingest_jobs": {
                "job-1": {"doc_name": "user_a.jpg", "status": "queued"}
            }
        }
    )
    page_index = SimpleNamespace(list_documents=AsyncMock(return_value=[]))
    dv_action = SimpleNamespace(
        agent_id="n.Agent.a",
        get_job_status=AsyncMock(return_value={"status": "failed"}),
        confirm_artifact_imported=AsyncMock(),
    )

    async def fake_get_action(name):
        if name == "PageIndexAction":
            return page_index
        if name == "ArtifactHandlerInteractAction":
            return dv_action
        return None

    ctx = SimpleNamespace(
        interview=SimpleNamespace(get_action=fake_get_action),
        visitor=SimpleNamespace(user_id="u1"),
        add_directive=lambda _msg: None,
    )
    result = await ct._maybe_refresh_pending_jobs(
        ctx, conv, say_ready=False, session_id="s1", user_id="u1"
    )
    assert result["failed"] == ["user_a.jpg"]
    assert result["still_queued"] == []
    assert (
        conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]["status"]
        == "failed"
    )
    dv_action.confirm_artifact_imported.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_processing_stays_queued():
    ct = _load_custom_tools()
    conv = FakeConversation(
        {
            "pending_ingest_jobs": {
                "job-1": {"doc_name": "user_a.jpg", "status": "queued"}
            }
        }
    )
    page_index = SimpleNamespace(list_documents=AsyncMock(return_value=[]))
    dv_action = SimpleNamespace(
        agent_id="n.Agent.a",
        get_job_status=AsyncMock(return_value={"status": "processing"}),
        confirm_artifact_imported=AsyncMock(),
    )

    async def fake_get_action(name):
        if name == "PageIndexAction":
            return page_index
        if name == "ArtifactHandlerInteractAction":
            return dv_action
        return None

    ctx = SimpleNamespace(
        interview=SimpleNamespace(get_action=fake_get_action),
        visitor=SimpleNamespace(user_id="u1"),
        add_directive=lambda _msg: None,
    )
    result = await ct._maybe_refresh_pending_jobs(
        ctx, conv, say_ready=False, session_id="s1", user_id="u1"
    )
    assert result["still_queued"] == ["user_a.jpg"]
    assert result["became_ready"] == []
    assert (
        conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]["status"]
        == "queued"
    )
