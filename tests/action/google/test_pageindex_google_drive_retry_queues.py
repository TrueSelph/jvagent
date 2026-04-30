"""Google Drive sync: retry queue selection and status derived from both queues."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from jvspatial.exceptions import ValidationError

from jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action import (
    PageIndexGoogleDriveSyncAction,
    _extract_and_prepend_queue_item,
    _find_file_dict_in_tree,
    _prune_added_queue_skip_existing,
    _strip_file_id_from_doc_queues,
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
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
            new_callable=AsyncMock,
            return_value=[],
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


def test_extract_and_prepend_queue_item_moves_match_to_front() -> None:
    q = {
        "added": [{"id": "a", "name": "a.pdf"}, {"id": "b", "name": "b.pdf"}],
        "modified": [],
        "removed": [],
    }
    assert _extract_and_prepend_queue_item(q, "b") is True
    assert [x["id"] for x in q["added"]] == ["b", "a"]


def test_extract_and_prepend_modified_bucket() -> None:
    q = {
        "added": [],
        "modified": [
            {"new": {"id": "x", "name": "x.pdf"}, "old": None},
            {"new": {"id": "y", "name": "y.pdf"}, "old": None},
        ],
        "removed": [],
    }
    assert _extract_and_prepend_queue_item(q, "y") is True
    assert [m["new"]["id"] for m in q["modified"]] == ["y", "x"]


def test_strip_file_id_from_doc_queues() -> None:
    q = {
        "added": [{"id": "a"}, {"id": "b"}],
        "modified": [{"new": {"id": "c"}, "old": None}],
        "removed": [{"id": "d"}],
    }
    _strip_file_id_from_doc_queues(q, "b")
    assert [x["id"] for x in q["added"]] == ["a"]
    _strip_file_id_from_doc_queues(q, "c")
    assert q["modified"] == []


def test_find_file_dict_in_tree_nested() -> None:
    tree = [
        {
            "id": "folder1",
            "mimeType": "application/vnd.google-apps.folder",
            "files": [
                {"id": "f1", "name": "n.pdf", "mimeType": "application/pdf"},
            ],
        }
    ]
    found = _find_file_dict_in_tree(tree, "f1")
    assert found is not None
    assert found["name"] == "n.pdf"


@pytest.mark.asyncio
async def test_prioritize_moves_to_front_of_ingesting_queue() -> None:
    node = SimpleNamespace(
        ingesting_documents={
            "added": [
                {"id": "a", "name": "a.pdf", "mimeType": "application/pdf"},
                {"id": "b", "name": "b.pdf", "mimeType": "application/pdf"},
            ],
            "modified": [],
            "removed": [],
        },
        failed_documents={"added": [], "modified": [], "removed": []},
        files=[],
        save=AsyncMock(return_value=None),
    )
    action = PageIndexGoogleDriveSyncAction(document_timeout=600)
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        out = await action.prioritize_google_drive_file_for_ingest("folder-1", "b")
    assert out["prioritized_in"] == "ingesting"
    assert [x["id"] for x in node.ingesting_documents["added"]] == ["b", "a"]


@pytest.mark.asyncio
async def test_prioritize_moves_to_front_of_failed_queue() -> None:
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={
            "added": [
                {"id": "a", "name": "a.pdf", "mimeType": "application/pdf"},
                {"id": "b", "name": "b.pdf", "mimeType": "application/pdf"},
            ],
            "modified": [],
            "removed": [],
        },
        files=[],
        save=AsyncMock(return_value=None),
    )
    action = PageIndexGoogleDriveSyncAction(document_timeout=600)
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        out = await action.prioritize_google_drive_file_for_ingest("folder-1", "b")
    assert out["prioritized_in"] == "failed"
    assert [x["id"] for x in node.failed_documents["added"]] == ["b", "a"]


@pytest.mark.asyncio
async def test_prioritize_enqueues_when_not_in_queues() -> None:
    f = {
        "id": "z1",
        "name": "z.pdf",
        "mimeType": "application/pdf",
        "url": "https://example.com/z",
    }
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={"added": [], "modified": [], "removed": []},
        files=[f],
        save=AsyncMock(return_value=None),
    )
    action = PageIndexGoogleDriveSyncAction(document_timeout=600)
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        out = await action.prioritize_google_drive_file_for_ingest("folder-1", "z1")
    assert out["prioritized_in"] == "enqueued"
    assert len(node.ingesting_documents["modified"]) == 1
    assert node.ingesting_documents["modified"][0]["new"]["id"] == "z1"


@pytest.mark.asyncio
async def test_prioritize_rejects_skip_ingest_enabled() -> None:
    f = {
        "id": "z1",
        "name": "z.pdf",
        "mimeType": "application/pdf",
        "disable_ingestion": True,
    }
    node = SimpleNamespace(
        ingesting_documents={"added": [], "modified": [], "removed": []},
        failed_documents={"added": [], "modified": [], "removed": []},
        files=[f],
        save=AsyncMock(return_value=None),
    )
    action = PageIndexGoogleDriveSyncAction(document_timeout=600)
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        with pytest.raises(ValidationError, match="Skip ingest"):
            await action.prioritize_google_drive_file_for_ingest("folder-1", "z1")


@pytest.mark.asyncio
async def test_clear_google_drive_file_from_queues_strips_both() -> None:
    node = SimpleNamespace(
        ingesting_documents={
            "added": [{"id": "a", "name": "a.pdf"}],
            "modified": [],
            "removed": [],
        },
        failed_documents={
            "added": [{"id": "b", "name": "b.pdf"}],
            "modified": [],
            "removed": [],
        },
        files=[],
        save=AsyncMock(return_value=None),
    )
    action = PageIndexGoogleDriveSyncAction(document_timeout=600)
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        out = await action.clear_google_drive_file_from_queues("folder-1", "a")
    assert out["cleared"] is True
    assert node.ingesting_documents["added"] == []
    with patch.object(
        PageIndexGoogleDriveSyncAction, "node", new_callable=AsyncMock
    ) as mock_node:
        mock_node.return_value = node
        out = await action.clear_google_drive_file_from_queues("folder-1", "b")
    assert node.failed_documents["added"] == []


@pytest.mark.asyncio
async def test_skip_existing_added_pops_queue_without_ingest() -> None:
    file_a = {
        "name": "ChargeReportForm.doc.md",
        "id": "file-a",
        "url": "https://example.com/a.pdf",
        "mimeType": "application/pdf",
    }
    node = SimpleNamespace(
        ingesting_documents={"added": [file_a], "modified": [], "removed": []},
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
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
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
            new_callable=AsyncMock,
            return_value=[{"doc_name": "ChargeReportForm.doc"}],
        ),
        patch(
            "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.assimilate_document",
            new_callable=AsyncMock,
        ) as mock_assimilate,
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
            source="ingesting_documents",
            skip_existing_documents=True,
        )

    assert out["success"] is True
    assert out.get("skipped") is True
    assert "already in index" in out["ingestion_message"]
    assert node.ingesting_documents["added"] == []
    mock_assimilate.assert_not_called()
    google_drive_action.get_media.assert_not_called()


@pytest.mark.asyncio
async def test_prune_added_skip_existing_removes_all_matches() -> None:
    """All added entries already in PageIndex are dropped in one pass (not one per ingest)."""
    node = SimpleNamespace(
        ingesting_documents={
            "added": [
                {"id": "1", "name": "a.pdf"},
                {"id": "2", "name": "b.pdf"},
                {"id": "3", "name": "legacy.doc.md"},
            ],
            "modified": [],
            "removed": [],
        },
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
        save=AsyncMock(return_value=None),
    )
    with patch(
        "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
        new_callable=AsyncMock,
        return_value=[
            {"doc_name": "a.pdf"},
            {"doc_name": "legacy.doc"},
        ],
    ):
        await _prune_added_queue_skip_existing(
            node, "agent-1", skip_existing_documents=True
        )
    assert [x["name"] for x in node.ingesting_documents["added"]] == ["b.pdf"]
    node.save.assert_called_once()


@pytest.mark.asyncio
async def test_prune_skip_existing_first_segment_charge_report_vs_md() -> None:
    """Indexed ``ChargeReportForm.doc`` prunes queued ``ChargeReportForm.doc.md`` (first segment)."""
    node = SimpleNamespace(
        ingesting_documents={
            "added": [
                {"id": "1", "name": "ChargeReportForm.doc.md"},
                {"id": "2", "name": "Other.pdf"},
            ],
            "modified": [],
            "removed": [],
        },
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
        save=AsyncMock(return_value=None),
    )
    with patch(
        "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
        new_callable=AsyncMock,
        return_value=[{"doc_name": "ChargeReportForm.doc"}],
    ):
        await _prune_added_queue_skip_existing(
            node, "agent-1", skip_existing_documents=True
        )
    assert [x["name"] for x in node.ingesting_documents["added"]] == ["Other.pdf"]


@pytest.mark.asyncio
async def test_prune_skip_existing_first_segment_no_false_positive() -> None:
    """Different first segments are not pruned when only another doc is indexed."""
    node = SimpleNamespace(
        ingesting_documents={
            "added": [{"id": "1", "name": "ChargeReportForm.doc.md"}],
            "modified": [],
            "removed": [],
        },
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
        save=AsyncMock(return_value=None),
    )
    with patch(
        "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
        new_callable=AsyncMock,
        return_value=[{"doc_name": "Other.pdf"}],
    ):
        await _prune_added_queue_skip_existing(
            node, "agent-1", skip_existing_documents=True
        )
    assert [x["name"] for x in node.ingesting_documents["added"]] == [
        "ChargeReportForm.doc.md"
    ]
    node.save.assert_not_called()


@pytest.mark.asyncio
async def test_prune_added_skip_existing_noop_when_flag_off() -> None:
    node = SimpleNamespace(
        ingesting_documents={
            "added": [{"id": "1", "name": "a.pdf"}],
            "modified": [],
            "removed": [],
        },
        failed_documents={"added": [], "modified": [], "removed": []},
        active_document="",
        status="pending",
        save=AsyncMock(return_value=None),
    )
    with patch(
        "jvagent.action.google.pageindex_google_drive_sync_action.pageindex_google_drive_sync_action.list_documents",
        new_callable=AsyncMock,
    ) as mock_list:
        await _prune_added_queue_skip_existing(
            node, "agent-1", skip_existing_documents=False
        )
    mock_list.assert_not_called()
    assert len(node.ingesting_documents["added"]) == 1
    node.save.assert_not_called()
