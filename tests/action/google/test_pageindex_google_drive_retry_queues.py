"""Google Drive sync: retry queue selection and status derived from both queues."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action import (
    PageIndexGoogleDriveSyncAction,
    _sync_drive_node_status_from_queues,
)


def test_sync_drive_node_status_respects_failed_backlog() -> None:
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={"added": [{"id": "a"}], "modified": [], "removed": []},
        active_document="",
        status="completed",
    )
    _sync_drive_node_status_from_queues(node)
    assert node.status == "failed"


def test_sync_drive_node_status_pending_when_ingesting_has_work() -> None:
    node = SimpleNamespace(
        ingesting_documents={"added": [{"id": "b"}], "modified": [], "removed": []},
        failed_documents={"added": [{"id": "a"}], "modified": [], "removed": []},
        active_document="",
        status="completed",
    )
    _sync_drive_node_status_from_queues(node)
    assert node.status == "pending"


def test_sync_drive_node_skips_while_active_document_set() -> None:
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={"added": [{"id": "a"}], "modified": [], "removed": []},
        active_document="doc.pdf",
        status="processing",
    )
    _sync_drive_node_status_from_queues(node)
    assert node.status == "processing"


@pytest.mark.asyncio
async def test_success_processing_failed_queue_keeps_failed_status_when_backlog_remains() -> (
    None
):
    file_a = {
        "name": "a.pdf",
        "id": "file-a",
        "url": "https://example.com/a.pdf",
        "mimeType": "application/pdf",
    }
    file_b = {
        "name": "b.pdf",
        "id": "file-b",
        "url": "https://example.com/b.pdf",
        "mimeType": "application/pdf",
    }
    failed_added = [file_a, file_b]
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={"added": failed_added, "modified": [], "removed": []},
        active_document="",
        status="failed",
        save=AsyncMock(return_value=None),
    )
    google_drive_action = SimpleNamespace(
        get_media=AsyncMock(return_value=b"%PDF-1.4 minimal"),
    )
    page_index_action = SimpleNamespace(get_webhook_url=AsyncMock(return_value=""))

    action = PageIndexGoogleDriveSyncAction(document_timeout=600)

    with (
        patch(
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.get_jvagent_jvforge_base_url",
            return_value="",
        ),
        patch(
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.assimilate_document",
            new_callable=AsyncMock,
            return_value={"doc_name": "a.pdf", "_root_id": "n.DocumentRootNode.x"},
        ),
    ):
        out = await action._process_single_document(
            google_drive_documents_node=node,
            google_drive_action=google_drive_action,
            file_info=file_a,
            doc_type="added",
            collection_name="agent-1",
            metadata={},
            model=None,
            model_action=None,
            node_summary="no",
            agent_id="agent-1",
            page_index_action=page_index_action,
            old_file=None,
            source="failed_documents",
        )

    assert out["success"] is True
    assert node.failed_documents["added"] == [file_b]
    assert node.status == "failed"
