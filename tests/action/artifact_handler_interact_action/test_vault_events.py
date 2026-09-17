"""Vault history events for saved documents and answered pending questions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action import (  # noqa: E501
    ArtifactHandlerInteractAction,
    _vault_doc_ids,
)
from jvagent.action.artifact_handler_interact_action.vault_events import (
    _VAULT_ACTION_NAME,
    answered_pending_event,
    record_vault_event,
    saved_document_event,
)
from jvagent.action.artifact_handler_interact_action.vault_tool_context import (
    VaultToolContext,
)
from jvagent.action.interact.utils.uploads import UploadItem
from tests.action.artifact_handler_interact_action.test_check_ingest_status_pending import (  # noqa: E501
    _ANSWER,
    _DOC_NAME,
    _PENDING_Q,
    FakeConversation,
    _empty_desc,
    _load_custom_tools,
    _no_refresh,
    _pending_job,
)

ct = _load_custom_tools()


class FakeInteraction:
    def __init__(self):
        self.events = []

    def add_event(self, event, action_name):
        self.events.append({"action_name": action_name, "content": event})
        return True

    async def save(self):
        return self


def test_saved_document_event_includes_pending_question():
    line = saved_document_event(
        _DOC_NAME, pending_question=_PENDING_Q, status="processing"
    )
    assert line.startswith(f"Saved document {_DOC_NAME}.")
    assert "Status: processing." in line
    assert _PENDING_Q in line
    assert "pageindex__search" not in line


def test_saved_document_event_omits_pending_when_missing():
    line = saved_document_event("user_report.pdf")
    assert line == "Saved document user_report.pdf. Status: processing."
    assert "Pending question" not in line


def test_answered_pending_event_pins_doc_name():
    line = answered_pending_event(_DOC_NAME, _PENDING_Q)
    assert f"document {_DOC_NAME}" in line
    assert "follow-ups" in line
    assert _PENDING_Q in line
    assert "pageindex__search" not in line
    assert len(line) < 500


@pytest.mark.asyncio
async def test_record_vault_event_writes_and_saves():
    interaction = FakeInteraction()
    line = saved_document_event("doc.pdf", pending_question="what is item one")
    await record_vault_event(interaction, line)
    assert interaction.events == [{"action_name": _VAULT_ACTION_NAME, "content": line}]


@pytest.mark.asyncio
async def test_record_vault_event_accepts_visitor():
    interaction = FakeInteraction()
    visitor = SimpleNamespace(interaction=interaction)
    await record_vault_event(visitor, answered_pending_event("a.jpg", "what color"))
    assert interaction.events[0]["content"].startswith(
        "Answered pending question on document a.jpg."
    )


@pytest.mark.asyncio
async def test_ingest_document_records_saved_event(monkeypatch):
    monkeypatch.setattr(ct, "_maybe_refresh_pending_jobs", _no_refresh)
    monkeypatch.setattr(ct, "_get_page_index_action", AsyncMock(return_value=object()))
    monkeypatch.setattr(ct, "_jvforge_configured", lambda: True)
    monkeypatch.setattr(ct, "_ensure_access_group", AsyncMock())
    monkeypatch.setattr(
        ct,
        "_get_artifact_handler_action",
        AsyncMock(
            return_value=SimpleNamespace(
                submit_ingest=AsyncMock(return_value={"job_id": "job-9"}),
                get_notify_webhook_url=AsyncMock(return_value="https://example/notify"),
            )
        ),
    )

    conv = FakeConversation({})
    interaction = FakeInteraction()
    visitor = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        conversation=conv,
        interaction=interaction,
    )
    ctx = VaultToolContext(
        visitor=visitor,
        args={
            "url": "https://example.com/photo.jpg",
            "question": _PENDING_Q,
        },
        action=SimpleNamespace(),
    )
    raw = await ct.ingest_document(ctx)
    assert "queued" in raw
    assert interaction.events
    content = interaction.events[0]["content"]
    assert content.startswith("Saved document")
    assert "photo.jpg" in content
    assert _PENDING_Q in content
    assert "Status: processing." in content
    assert "pageindex__search" not in content


@pytest.mark.asyncio
async def test_check_ingest_status_records_answered_event(monkeypatch):
    conv = FakeConversation(_pending_job())
    interaction = FakeInteraction()
    visitor = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        conversation=conv,
        interaction=interaction,
    )
    ctx = VaultToolContext(visitor=visitor, args={}, action=SimpleNamespace())
    monkeypatch.setattr(ct, "_maybe_refresh_pending_jobs", _no_refresh)
    monkeypatch.setattr(ct, "_doc_description_lookup", _empty_desc)
    monkeypatch.setattr(
        ct, "_generate_poll_ready_answer", AsyncMock(return_value=_ANSWER)
    )

    await ct.check_ingest_status(ctx)
    assert interaction.events
    content = interaction.events[0]["content"]
    assert content.startswith("Answered pending question on document")
    assert _DOC_NAME in content
    assert _PENDING_Q in content
    stored = conv.context["artifact_handler"]["pending_ingest_jobs"]["job-1"]
    assert "pending_question" not in stored


@pytest.mark.asyncio
async def test_media_auto_ingest_records_saved_event(monkeypatch):
    import jvagent.action.artifact_handler_interact_action.artifact_handler_interact_action as ah

    item = UploadItem(
        filename="photo.jpeg",
        mime="image/jpeg",
        kind="image",
        raw=b"fake-bytes",
    )
    expected_doc, _ = _vault_doc_ids("user-1", "photo.jpeg")
    interaction = FakeInteraction()
    conv = FakeConversation({})
    visitor = SimpleNamespace(
        session_id="sess-1",
        user_id="user-1",
        conversation=conv,
        interaction=interaction,
        utterance=_PENDING_Q,
        channel="web",
        add_directive=AsyncMock(),
        unrecord_action_execution=AsyncMock(),
        curate_walk_path=AsyncMock(),
    )
    action = ArtifactHandlerInteractAction()
    monkeypatch.setattr(
        ArtifactHandlerInteractAction,
        "get_action",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        ArtifactHandlerInteractAction, "_ensure_access_group", AsyncMock()
    )
    monkeypatch.setattr(
        ArtifactHandlerInteractAction, "_jvforge_configured", lambda self: True
    )
    monkeypatch.setattr(
        ArtifactHandlerInteractAction,
        "get_notify_webhook_url",
        AsyncMock(return_value="https://example/notify"),
    )
    monkeypatch.setattr(
        ArtifactHandlerInteractAction,
        "submit_ingest",
        AsyncMock(return_value={"job_id": "job-media"}),
    )
    monkeypatch.setattr(
        ArtifactHandlerInteractAction,
        "_inject_accessible_documents_parameter",
        AsyncMock(),
    )
    monkeypatch.setattr(ArtifactHandlerInteractAction, "respond", AsyncMock())
    monkeypatch.setattr(ah, "_collect_visitor_media", lambda _v: ([item], False))
    monkeypatch.setattr(
        ah, "_save_upload_to_files", AsyncMock(return_value="https://files/photo.jpeg")
    )

    await action.execute(visitor)

    assert interaction.events
    content = interaction.events[0]["content"]
    assert content.startswith("Saved document")
    assert expected_doc in content
    assert _PENDING_Q in content
    assert "Status: processing." in content
    assert "pageindex__search" not in content
