"""Post-ingest Google Drive sync state: no double-pop when save fails after ingest."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action import (
    PageIndexGoogleDriveSyncAction,
)


@pytest.mark.asyncio
async def test_post_ingest_save_failure_single_pop_success_returned():
    """After successful ingest, a failing node.save() must not trigger a second queue pop."""
    file_info = {
        "name": "doc.pdf",
        "id": "file-1",
        "url": "https://example.com/doc.pdf",
        "mimeType": "application/pdf",
    }
    added_queue = [file_info]
    node = SimpleNamespace(
        ingesting_documents={"added": added_queue, "modified": [], "removed": []},
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
        save=AsyncMock(
            side_effect=[
                None,  # initial _process_single_document save
                RuntimeError("persist failed"),  # post-ingest save
            ]
        ),
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
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.assimilate_document",
            new_callable=AsyncMock,
            return_value={"doc_name": "doc.pdf", "_root_id": "n.DocumentRootNode.x"},
        ),
    ):
        out = await action._process_single_document(
            google_drive_documents_node=node,
            google_drive_action=google_drive_action,
            file_info=file_info,
            doc_type="added",
            collection_name="agent-1",
            metadata={},
            model=None,
            model_action=None,
            node_summary="no",
            agent_id="agent-1",
            page_index_action=page_index_action,
            old_file=None,
        )

    assert out["success"] is True
    assert "warning: sync state not updated" in out["ingestion_message"]
    assert node.ingesting_documents["added"] == []
    assert node.failed_documents["added"] == []
