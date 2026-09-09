"""Tests for Google Drive PageIndex ingest filtering."""

from jvagent.action.pageindex.pageindex_google_drive_sync_action.drive_ingest_filter import (
    file_ids_under_excluded_folders,
    filter_drive_doc_queues_for_ingestible,
    guess_pageindex_extension,
    is_drive_file_pageindex_ingestible,
    is_drive_file_video,
    mark_drive_video_files_disabled,
    prune_excluded_sub_folders,
)


def test_exe_not_ingestible():
    assert not is_drive_file_pageindex_ingestible(
        "GYS-1701.exe", "application/x-msdownload"
    )


def test_pdf_ingestible():
    assert is_drive_file_pageindex_ingestible("report.pdf", "application/pdf")


def test_extensionless_pdf_ingestible_from_mime():
    assert is_drive_file_pageindex_ingestible("Report", "application/pdf")
    assert guess_pageindex_extension("Report", "application/pdf") == ".pdf"


def test_extensionless_word_ingestible_from_mime():
    assert is_drive_file_pageindex_ingestible(
        "Memo",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert (
        guess_pageindex_extension(
            "Memo",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        == ".docx"
    )


def test_extensionless_video_not_ingestible():
    assert not is_drive_file_pageindex_ingestible("Clip", "video/mp4")
    assert guess_pageindex_extension("Clip", "video/mp4") == ""


def test_octet_stream_without_suffix_not_ingestible():
    assert not is_drive_file_pageindex_ingestible("unknown", "application/octet-stream")
    assert guess_pageindex_extension("unknown", "application/octet-stream") == ""


def test_google_doc_native_ingestible():
    assert is_drive_file_pageindex_ingestible(
        "My Doc",
        "application/vnd.google-apps.document",
    )


def test_shortcut_without_target_skipped():
    assert not is_drive_file_pageindex_ingestible(
        "Link to file",
        "application/vnd.google-apps.shortcut",
    )


def test_shortcut_to_google_doc_ingestible():
    assert is_drive_file_pageindex_ingestible(
        "TCS Updates",
        "application/vnd.google-apps.shortcut",
        {"targetId": "doc1", "targetMimeType": "application/vnd.google-apps.document"},
    )


def test_shortcut_to_pdf_ingestible():
    assert is_drive_file_pageindex_ingestible(
        "TCS Updates",
        "application/vnd.google-apps.shortcut",
        {"targetId": "pdf1", "targetMimeType": "application/pdf"},
    )


def test_shortcut_to_video_not_ingestible():
    assert not is_drive_file_pageindex_ingestible(
        "Clip",
        "application/vnd.google-apps.shortcut",
        {"targetId": "vid1", "targetMimeType": "video/mp4"},
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


def test_filter_queues_keeps_shortcut_to_google_doc():
    docs = {
        "added": [
            {
                "id": "1",
                "name": "TCS Updates",
                "mimeType": "application/vnd.google-apps.shortcut",
                "shortcutDetails": {
                    "targetId": "doc1",
                    "targetMimeType": "application/vnd.google-apps.document",
                },
            },
            {
                "id": "2",
                "name": "Link",
                "mimeType": "application/vnd.google-apps.shortcut",
            },
        ],
        "modified": [],
        "removed": [],
    }
    filter_drive_doc_queues_for_ingestible(docs)
    assert len(docs["added"]) == 1
    assert docs["added"][0]["id"] == "1"


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


_FOLDER_MIME = "application/vnd.google-apps.folder"


def _folder(fid: str, name: str, nested: list) -> dict:
    return {
        "id": fid,
        "name": name,
        "mimeType": _FOLDER_MIME,
        "files": nested,
    }


def test_prune_excluded_sub_folders_by_id():
    files = [
        {"id": "1", "name": "a.pdf", "mimeType": "application/pdf"},
        _folder(
            "f1",
            "Keep",
            [
                {"id": "2", "name": "b.pdf", "mimeType": "application/pdf"},
                _folder(
                    "f2",
                    "SkipMe",
                    [{"id": "3", "name": "c.pdf", "mimeType": "application/pdf"}],
                ),
            ],
        ),
        _folder(
            "f3", "Drop", [{"id": "4", "name": "d.pdf", "mimeType": "application/pdf"}]
        ),
    ]
    pruned = prune_excluded_sub_folders(files, ["f2", "f3"])
    assert pruned == ["f2", "f3"]
    assert files[1]["files"] == [
        {"id": "2", "name": "b.pdf", "mimeType": "application/pdf"}
    ]
    assert len(files) == 2


def test_prune_excluded_sub_folders_by_name():
    files = [
        _folder(
            "f1",
            "Archive",
            [{"id": "2", "name": "old.pdf", "mimeType": "application/pdf"}],
        ),
        {"id": "3", "name": "new.pdf", "mimeType": "application/pdf"},
    ]
    pruned = prune_excluded_sub_folders(files, ["Archive"])
    assert pruned == ["f1"]
    assert len(files) == 1


def test_prune_excluded_sub_folders_no_match():
    files = [
        _folder(
            "f1", "Keep", [{"id": "2", "name": "b.pdf", "mimeType": "application/pdf"}]
        ),
    ]
    pruned = prune_excluded_sub_folders(files, ["missing-id", "Nope"])
    assert pruned == []
    assert files[0]["files"][0]["id"] == "2"


def test_prune_excluded_sub_folders_empty_or_none():
    files = [_folder("f1", "Archive", [])]
    assert prune_excluded_sub_folders(files, []) == []
    assert prune_excluded_sub_folders(files, None) == []
    assert files[0]["files"] == []


def test_prune_excluded_sub_folders_strips_whitespace():
    files = [_folder("f1", "Archive", [])]
    pruned = prune_excluded_sub_folders(files, ["  f1  "])
    assert pruned == ["f1"]
    assert files == []


def test_prune_excluded_sub_folders_name_trailing_space():
    files = [_folder("f1", "Product Specification ", [])]
    pruned = prune_excluded_sub_folders(files, ["Product Specification"])
    assert pruned == ["f1"]
    assert files == []


def test_prune_excluded_sub_folders_does_not_touch_files():
    files = [{"id": "1", "name": "Archive", "mimeType": "application/pdf"}]
    pruned = prune_excluded_sub_folders(files, ["Archive"])
    assert pruned == []
    assert len(files) == 1


def test_file_ids_under_excluded_folders_by_id_and_name():
    files = [
        {"id": "keep", "name": "keep.pdf", "mimeType": "application/pdf"},
        _folder(
            "f1",
            "Archive",
            [{"id": "skip-me", "name": "old.pdf", "mimeType": "application/pdf"}],
        ),
        _folder(
            "f2",
            "Keep",
            [{"id": "ok", "name": "new.pdf", "mimeType": "application/pdf"}],
        ),
    ]
    assert file_ids_under_excluded_folders(files, ["f1"]) == ["skip-me"]
    assert file_ids_under_excluded_folders(files, ["Archive"]) == ["skip-me"]
    assert file_ids_under_excluded_folders(files, []) == []
    assert file_ids_under_excluded_folders(files, ["Keep"]) == ["ok"]


def test_file_ids_under_excluded_folders_does_not_include_root_files():
    files = [
        {"id": "keep", "name": "keep.pdf", "mimeType": "application/pdf"},
        _folder(
            "f3",
            "Drop",
            [{"id": "skip", "name": "skip.pdf", "mimeType": "application/pdf"}],
        ),
    ]
    assert file_ids_under_excluded_folders(files, ["f3"]) == ["skip"]
