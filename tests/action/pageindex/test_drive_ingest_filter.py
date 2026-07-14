"""Tests for Google Drive PageIndex ingest filtering."""

from jvagent.action.pageindex.pageindex_google_drive_sync_action.drive_ingest_filter import (
    filter_drive_doc_queues_for_ingestible,
    is_drive_file_pageindex_ingestible,
    is_drive_file_video,
    mark_drive_video_files_disabled,
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


def test_is_drive_file_video_extensions():
    assert is_drive_file_video("clip.mp4", "video/mp4")
    assert is_drive_file_video("clip.MOV", "video/quicktime")
    assert is_drive_file_video("clip.mkv", "")
    assert not is_drive_file_video("song.mp3", "audio/mpeg")
    assert not is_drive_file_video("report.pdf", "application/pdf")


def test_is_drive_file_video_google_apps_mime():
    assert is_drive_file_video("My Video", "application/vnd.google-apps.video")
    assert not is_drive_file_video("My Doc", "application/vnd.google-apps.document")


def test_mark_drive_video_files_disabled_flat():
    files = [
        {"id": "1", "name": "a.pdf", "mimeType": "application/pdf"},
        {"id": "2", "name": "b.mp4", "mimeType": "video/mp4"},
        {"id": "3", "name": "c.mov", "mimeType": "video/quicktime"},
        {"id": "4", "name": "d.txt", "mimeType": "text/plain"},
    ]
    mark_drive_video_files_disabled(files)
    assert files[0].get("disable_ingestion") is not True
    assert files[1].get("disable_ingestion") is True
    assert files[2].get("disable_ingestion") is True
    assert files[3].get("disable_ingestion") is not True


def test_mark_drive_video_files_disabled_nested():
    files = [
        {
            "id": "f1",
            "name": "Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "files": [
                {"id": "2", "name": "b.mp4", "mimeType": "video/mp4"},
                {"id": "5", "name": "e.pdf", "mimeType": "application/pdf"},
            ],
        },
        {"id": "6", "name": "g.avi", "mimeType": "video/x-msvideo"},
    ]
    mark_drive_video_files_disabled(files)
    assert files[0].get("disable_ingestion") is not True
    assert files[0]["files"][0].get("disable_ingestion") is True
    assert files[0]["files"][1].get("disable_ingestion") is not True
    assert files[1].get("disable_ingestion") is True


def test_mark_drive_video_files_disabled_google_apps_video():
    files = [
        {
            "id": "1",
            "name": "Drive Video",
            "mimeType": "application/vnd.google-apps.video",
        },
        {"id": "2", "name": "Doc", "mimeType": "application/vnd.google-apps.document"},
    ]
    mark_drive_video_files_disabled(files)
    assert files[0].get("disable_ingestion") is True
    assert files[1].get("disable_ingestion") is not True


def test_mark_drive_video_files_disabled_overwrites_prior():
    files = [
        {
            "id": "1",
            "name": "b.mp4",
            "mimeType": "video/mp4",
            "disable_ingestion": False,
        },
    ]
    mark_drive_video_files_disabled(files)
    assert files[0].get("disable_ingestion") is True


def test_mark_drive_video_files_disabled_skips_shortcut():
    files = [
        {
            "id": "1",
            "name": "Link",
            "mimeType": "application/vnd.google-apps.shortcut",
        },
        {"id": "2", "name": "b.mp4", "mimeType": "video/mp4"},
    ]
    mark_drive_video_files_disabled(files)
    assert "disable_ingestion" not in files[0]
    assert files[1].get("disable_ingestion") is True
