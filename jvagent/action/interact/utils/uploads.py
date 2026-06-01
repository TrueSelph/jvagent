"""Upload normalization helpers for artifact ingestion (ADR-0021 S4).

Files arrive in ``visitor.data`` under keys like ``image_urls`` /
``whatsapp_media`` (and generic ``files`` / ``attachments`` / ``documents``) as
either a bare URL string or a mapping ``{url|base64, mime_type, filename}``.
These pure helpers normalize an entry, classify it (image / text / file), and
decode text payloads — the orchestrator's ``_ingest_uploads`` persists the bytes
and writes one artifact per file.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import urlparse

# Default ``data`` keys scanned for uploads. Web/jvchat uses image_urls +
# whatsapp_media; the generic trio covers other clients/channels.
DEFAULT_UPLOAD_KEYS = (
    "image_urls",
    "whatsapp_media",
    "files",
    "attachments",
    "documents",
)

# Non-``text/*`` MIME types that are nonetheless text payloads worth decoding
# into the artifact's queryable ``data``.
_TEXTUAL_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/x-sh",
        "application/csv",
        "application/sql",
        "image/svg+xml",  # XML text
    }
)
_TEXTUAL_SUFFIXES = ("+json", "+xml", "+yaml")


@dataclass
class UploadItem:
    """One normalized upload from ``visitor.data``."""

    filename: str
    mime: str
    kind: str  # "image" | "text" | "file"
    raw: Optional[bytes] = None  # decoded bytes when an inline base64 was given
    url: str = ""  # remote URL when the entry referenced one (no bytes inline)

    @property
    def size(self) -> int:
        return len(self.raw) if self.raw is not None else 0


def _guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed
    return fallback


def _filename_from_url(url: str) -> str:
    try:
        tail = os.path.basename(urlparse(url).path)
    except Exception:
        tail = ""
    return tail or "download"


def is_textual_mime(mime: str) -> bool:
    m = (mime or "").split(";", 1)[0].strip().lower()
    if not m:
        return False
    if m.startswith("text/"):
        return True
    if m in _TEXTUAL_MIMES:
        return True
    return any(m.endswith(suf) for suf in _TEXTUAL_SUFFIXES)


def classify_kind(mime: str) -> str:
    m = (mime or "").split(";", 1)[0].strip().lower()
    if m.startswith("image/") and m != "image/svg+xml":
        return "image"
    if is_textual_mime(m):
        return "text"
    return "file"


def normalize_upload_entry(entry: Any) -> Optional[UploadItem]:
    """Normalize one ``data`` upload entry, or None if it carries no usable file.

    Accepts a bare URL string or a ``{url|base64, mime_type, filename}`` mapping.
    Inline base64 is decoded to bytes; URL entries keep ``url`` (no fetch here —
    that's the caller's policy decision, and avoids SSRF in this pure helper).
    """
    url = ""
    raw: Optional[bytes] = None
    filename = ""
    mime = ""

    if isinstance(entry, str):
        url = entry.strip()
        if not url:
            return None
        filename = _filename_from_url(url)
    elif isinstance(entry, dict):
        filename = str(entry.get("filename") or "").strip()
        mime = str(entry.get("mime_type") or entry.get("mime") or "").strip()
        b64 = entry.get("base64")
        if b64:
            payload = (
                b64.split(",", 1)[1] if isinstance(b64, str) and "," in b64 else b64
            )
            try:
                raw = base64.b64decode(payload, validate=False)
            except (binascii.Error, ValueError, TypeError):
                raw = None
        if not raw:
            url = str(entry.get("url") or "").strip()
        if not filename:
            filename = _filename_from_url(url) if url else "upload"
    else:
        return None

    if raw is None and not url:
        return None

    if not mime:
        mime = _guess_mime(filename)
    return UploadItem(
        filename=filename or "upload",
        mime=mime,
        kind=classify_kind(mime),
        raw=raw,
        url=url,
    )


def collect_uploads(data: Any, keys: Any = DEFAULT_UPLOAD_KEYS) -> List[UploadItem]:
    """Normalize every upload entry across ``keys`` in a ``visitor.data`` dict."""
    if not isinstance(data, dict):
        return []
    out: List[UploadItem] = []
    for key in keys:
        entries = data.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            item = normalize_upload_entry(entry)
            if item is not None:
                out.append(item)
    return out


def decode_text(raw: bytes, *, max_chars: int = 20000) -> str:
    """Decode text-file bytes (utf-8, lenient), bounded for the artifact payload."""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text[:max_chars]


def human_size(n: int) -> str:
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(n)} B"


__all__ = [
    "DEFAULT_UPLOAD_KEYS",
    "UploadItem",
    "classify_kind",
    "is_textual_mime",
    "normalize_upload_entry",
    "collect_uploads",
    "decode_text",
    "human_size",
]
