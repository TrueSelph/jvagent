"""Filter Google Drive file list to types PageIndex / jvforge can ingest."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from jvagent.action.pageindex.documents import PAGEINDEX_UPLOAD_EXTENSIONS

_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_GOOGLE_APPS_PREFIX = "application/vnd.google-apps."

_GOOGLE_APPS_NON_DOCUMENT_MIMES = frozenset(
    {
        "application/vnd.google-apps.video",
        "application/vnd.google-apps.audio",
        "application/vnd.google-apps.photo",
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.map",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.jam",
    }
)

_GOOGLE_APPS_VIDEO_MIME = "application/vnd.google-apps.video"

_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
    }
)

_NON_INGESTIBLE_EXTENSIONS = frozenset(
    _VIDEO_EXTENSIONS
    | {
        ".mp3",
        ".wav",
        ".aac",
        ".flac",
        ".ogg",
        ".wma",
        ".m4a",
        ".zip",
        ".tar",
        ".gz",
        ".rar",
        ".7z",
        ".bz2",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".dmg",
        ".iso",
        ".img",
    }
)


def is_drive_file_pageindex_ingestible(name: str, mime_type: str) -> bool:
    """Return True if the file should be queued for PageIndex (matches jvforge allowlist + Drive export).

    Google Workspace native documents/spreadsheets/presentations/drawings are
    exported as PDF in ``get_media``.  Google Workspace video, audio, photo, and
    other non-document types cannot be exported to PDF and are skipped.  Shortcuts
    and folders are also skipped.  Regular files are checked against the
    PageIndex extension allowlist.
    """
    mt = (mime_type or "").strip()
    if mt == _FOLDER_MIME:
        return False
    if mt == _SHORTCUT_MIME:
        return False
    if mt in _GOOGLE_APPS_NON_DOCUMENT_MIMES:
        return False
    if mt.startswith(_GOOGLE_APPS_PREFIX):
        return True
    ext = Path(name or "").suffix.lower()
    if ext in _NON_INGESTIBLE_EXTENSIONS:
        return False
    return ext in PAGEINDEX_UPLOAD_EXTENSIONS


def _queue_item_ingestible(item: Any, queue_key: str) -> bool:
    if not isinstance(item, dict):
        return False
    if queue_key == "modified":
        new = item.get("new")
        if isinstance(new, dict):
            return is_drive_file_pageindex_ingestible(
                str(new.get("name") or ""),
                str(new.get("mimeType") or ""),
            )
        return is_drive_file_pageindex_ingestible(
            str(item.get("name") or ""),
            str(item.get("mimeType") or ""),
        )
    return is_drive_file_pageindex_ingestible(
        str(item.get("name") or ""),
        str(item.get("mimeType") or ""),
    )


def filter_drive_doc_queues_for_ingestible(docs: Dict[str, Any]) -> None:
    """Drop unsupported files from added/modified/removed queues in place."""
    for key in ("added", "modified", "removed"):
        raw = list(docs.get(key) or [])
        docs[key] = [x for x in raw if _queue_item_ingestible(x, key)]


def is_drive_file_video(name: str, mime_type: str) -> bool:
    """Return True for Google Drive video items (Google Apps video mime or video file ext)."""
    mt = (mime_type or "").strip()
    if mt == _GOOGLE_APPS_VIDEO_MIME:
        return True
    ext = Path(name or "").suffix.lower()
    return ext in _VIDEO_EXTENSIONS


def mark_drive_video_files_disabled(files: List[Dict[str, Any]]) -> None:
    """Set ``disable_ingestion=True`` on every video file in a nested Drive ``files`` tree.

    Folders and shortcuts are skipped so traversal / nesting is not disrupted.
    Mutates the tree in place; returns nothing.
    """
    for it in files:
        if not isinstance(it, dict):
            continue
        mt = str(it.get("mimeType") or "")
        if mt != _FOLDER_MIME and mt != _SHORTCUT_MIME:
            if is_drive_file_video(str(it.get("name") or ""), mt):
                it["disable_ingestion"] = True
        nested = it.get("files")
        if isinstance(nested, list):
            mark_drive_video_files_disabled(nested)
