"""Tests for Google Drive PageIndex ingest filtering."""

from jvagent.action.google.pageindex_google_drive_sync_action.drive_ingest_filter import (
    filter_drive_doc_queues_for_ingestible,
    is_drive_file_pageindex_ingestible,
)


def test_exe_not_ingestible():
    assert not is_drive_file_pageindex_ingestible(
        "GYS-1701.exe", "application/x-msdownload"
    )


def test_pdf_ingestible():
    assert is_drive_file_pageindex_ingestible("report.pdf", "application/pdf")


def test_google_doc_native_ingestible():
    assert is_drive_file_pageindex_ingestible(
        "My Doc",
        "application/vnd.google-apps.document",
    )


def test_shortcut_skipped():
    assert not is_drive_file_pageindex_ingestible(
        "Link to file",
        "application/vnd.google-apps.shortcut",
    )


def test_filter_queues_drops_exe():
    docs = {
        "added": [
            {"id": "1", "name": "a.pdf", "mimeType": "application/pdf"},
            {"id": "2", "name": "b.exe", "mimeType": "application/x-msdownload"},
        ],
        "modified": [],
        "removed": [],
    }
    filter_drive_doc_queues_for_ingestible(docs)
    assert len(docs["added"]) == 1
    assert docs["added"][0]["name"] == "a.pdf"


def test_filter_modified_uses_new_dict():
    docs = {
        "added": [],
        "modified": [
            {
                "id": "m1",
                "old": {"id": "m1", "name": "old.pdf"},
                "new": {
                    "id": "m1",
                    "name": "bad.exe",
                    "mimeType": "application/x-msdownload",
                },
            },
        ],
        "removed": [],
    }
    filter_drive_doc_queues_for_ingestible(docs)
    assert docs["modified"] == []
