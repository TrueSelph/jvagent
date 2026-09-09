"""Filter Google Drive file list to types PageIndex / jvforge can ingest."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Drive often stores PDFs/Office files with no suffix; mime is the source of truth.
_MIME_TO_PAGEINDEX_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "image/jpeg": ".jpeg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}

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


def guess_pageindex_extension(name: str, mime_type: str) -> str:
    """Return a PageIndex upload extension from filename suffix or Drive mime.

    Empty string when the type cannot be mapped onto ``PAGEINDEX_UPLOAD_EXTENSIONS``.
    """
    ext = Path(name or "").suffix.lower()
    if ext in PAGEINDEX_UPLOAD_EXTENSIONS:
        return ext
    mt = (mime_type or "").strip().split(";", 1)[0].strip().lower()
    if not mt:
        return ""
    if mt in ("text/markdown", "text/x-markdown"):
        guessed = ".md"
    elif mt in _MIME_TO_PAGEINDEX_EXT:
        guessed = _MIME_TO_PAGEINDEX_EXT[mt]
    else:
        guessed = (mimetypes.guess_extension(mt) or "").lower()
        if guessed == ".jpe":
            guessed = ".jpeg"
    if guessed in PAGEINDEX_UPLOAD_EXTENSIONS:
        return guessed
    return ""


def effective_drive_ingest_mime(
    mime_type: str, shortcut_details: Optional[Dict[str, Any]] = None
) -> str:
    """Mime used for ingest: shortcut rows use ``targetMimeType``."""
    mt = (mime_type or "").strip()
    if mt != _SHORTCUT_MIME:
        return mt
    if not isinstance(shortcut_details, dict):
        return ""
    return str(shortcut_details.get("targetMimeType") or "").strip()


def is_drive_file_pageindex_ingestible(
    name: str,
    mime_type: str,
    shortcut_details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True if the file should be queued for PageIndex (matches jvforge allowlist + Drive export).

    Google Workspace native documents/spreadsheets/presentations/drawings are
    exported as PDF in ``get_media``.  Google Workspace video, audio, photo, and
    other non-document types cannot be exported to PDF and are skipped.  Folders
    are skipped.  Shortcuts are typed from ``shortcutDetails.targetMimeType``
    (a shortcut to a Google Doc is ingestible; a shortcut with no target mime
    is not).  Regular files use the filename suffix when present, otherwise the
    Drive mime type, against the PageIndex allowlist.
    """
    mt = (mime_type or "").strip()
    if mt == _FOLDER_MIME:
        return False
    if mt == _SHORTCUT_MIME:
        target = effective_drive_ingest_mime(mime_type, shortcut_details)
        if not target or target == _SHORTCUT_MIME:
            return False
        return is_drive_file_pageindex_ingestible(name, target)
    if mt in _GOOGLE_APPS_NON_DOCUMENT_MIMES:
        return False
    if mt.startswith(_GOOGLE_APPS_PREFIX):
        return True
    ext = Path(name or "").suffix.lower()
    if ext in _NON_INGESTIBLE_EXTENSIONS:
        return False
    return bool(guess_pageindex_extension(name, mt))


def _item_shortcut_details(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    d = item.get("shortcutDetails")
    return d if isinstance(d, dict) else None


def _queue_item_ingestible(item: Any, queue_key: str) -> bool:
    if not isinstance(item, dict):
        return False
    if queue_key == "modified":
        new = item.get("new")
        if isinstance(new, dict):
            return is_drive_file_pageindex_ingestible(
                str(new.get("name") or ""),
                str(new.get("mimeType") or ""),
                _item_shortcut_details(new),
            )
        return is_drive_file_pageindex_ingestible(
            str(item.get("name") or ""),
            str(item.get("mimeType") or ""),
            _item_shortcut_details(item),
        )
    return is_drive_file_pageindex_ingestible(
        str(item.get("name") or ""),
        str(item.get("mimeType") or ""),
        _item_shortcut_details(item),
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


def file_ids_under_excluded_folders(
    files: List[Dict[str, Any]], excluded: List[str]
) -> List[str]:
    """File ids whose ancestor folder id or exact name is in ``excluded``."""
    excluded_keys = {str(e).strip() for e in (excluded or []) if str(e).strip()}
    if not excluded_keys:
        return []
    out: List[str] = []

    def walk(items: List[Dict[str, Any]], excluded_branch: bool) -> None:
        for it in items:
            if not isinstance(it, dict):
                continue
            mt = str(it.get("mimeType") or "")
            is_folder = mt == _FOLDER_MIME
            this_excluded = excluded_branch or (
                is_folder
                and (
                    str(it.get("id") or "") in excluded_keys
                    or str(it.get("name") or "").strip() in excluded_keys
                )
            )
            if not is_folder and this_excluded and it.get("id"):
                out.append(str(it["id"]))
            nested = it.get("files")
            if isinstance(nested, list):
                walk(nested, this_excluded)

    walk(files, False)
    return out


def prune_excluded_sub_folders(
    files: List[Dict[str, Any]], excluded: List[str]
) -> List[str]:
    """Remove excluded subfolders (and their subtrees) from a nested Drive ``files`` tree.

    A folder is excluded when its ``id`` or ``name`` matches an entry in
    ``excluded`` (string comparison, entries stripped of whitespace).  Matching
    folders are dropped from their parent's ``files`` list in place, so their
    contents never reach the ingest queues.  Returns the ids of pruned folders.
    """
    excluded_keys = {str(e).strip() for e in (excluded or []) if str(e).strip()}
    if not excluded_keys:
        return []

    pruned_ids: List[str] = []

    def walk(items: List[Dict[str, Any]]) -> None:
        kept: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            mt = str(it.get("mimeType") or "")
            if mt == _FOLDER_MIME and (
                str(it.get("id") or "") in excluded_keys
                or str(it.get("name") or "").strip() in excluded_keys
            ):
                if it.get("id"):
                    pruned_ids.append(str(it["id"]))
                continue
            nested = it.get("files")
            if isinstance(nested, list):
                walk(nested)
            kept.append(it)
        items[:] = kept

    walk(files)
    return pruned_ids


def mark_drive_video_files_disabled(files: List[Dict[str, Any]]) -> None:
    """Set ``disable_ingestion=True`` on every video file in a nested Drive ``files`` tree.

    Folders are skipped so traversal / nesting is not disrupted. Shortcuts to
    videos are disabled; other shortcuts are left for ingest to follow.
    Mutates the tree in place; returns nothing.
    """
    for it in files:
        if not isinstance(it, dict):
            continue
        mt = str(it.get("mimeType") or "")
        if mt != _FOLDER_MIME:
            if mt == _SHORTCUT_MIME:
                target_mt = effective_drive_ingest_mime(
                    mt,
                    (
                        it.get("shortcutDetails")
                        if isinstance(it.get("shortcutDetails"), dict)
                        else None
                    ),
                )
                if is_drive_file_video(str(it.get("name") or ""), target_mt):
                    it["disable_ingestion"] = True
            elif is_drive_file_video(str(it.get("name") or ""), mt):
                it["disable_ingestion"] = True
        nested = it.get("files")
        if isinstance(nested, list):
            mark_drive_video_files_disabled(nested)
