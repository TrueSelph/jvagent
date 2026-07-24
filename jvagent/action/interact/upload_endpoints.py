"""Public attachment-upload endpoint for the embeddable messenger.

``POST /agents/{agent_id}/uploads`` accepts one ``multipart/form-data`` file,
persists it via the App storage abstraction, and returns a URL the messenger then
passes in the next ``/interact`` call's ``data.image_urls`` / ``data.attachments``
(consumed by the vision pipeline). ``auth=False`` but always gated by a valid
``X-Session-Token`` (see :mod:`jvagent.action.interact.public_gate`), so uploads
are tied to an established conversation.

A dedicated multipart endpoint is used instead of inline base64 in ``interact``:
base64 inflates payloads ~33% and would collide with the media-aware interact
size caps on every turn. Here the bytes land once in storage and only a URL
travels on the wire thereafter.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ValidationError

from jvagent.action.interact.public_gate import require_messenger_session
from jvagent.action.interact.rate_limiter import DEFAULT_MAX_UPLOAD_ITEM_BYTES
from jvagent.core.public_url import get_public_base_url

logger = logging.getLogger(__name__)

# Accepted upload MIME types (images the vision pipeline understands + common
# documents customers attach). Kept conservative; widen via config if needed.
_ALLOWED_MIME_PREFIXES = ("image/",)
_ALLOWED_MIME_EXACT = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "").strip()) or "upload"
    return cleaned[:120]


def _mime_allowed(mime: str) -> bool:
    mime = (mime or "").split(";", 1)[0].strip().lower()
    if mime in _ALLOWED_MIME_EXACT:
        return True
    return any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES)


@endpoint(
    "/agents/{agent_id}/uploads",
    methods=["POST"],
    auth=False,
    tags=["Agent"],
)
async def upload_endpoint(request: Request, agent_id: str) -> Any:
    """Store one uploaded file and return a URL for use in a later interact call."""
    _agent, claims = await require_messenger_session(request, agent_id)
    session_id = str(claims.get("session_id") or "unknown")

    try:
        form = await request.form()
    except Exception:
        raise ValidationError(message="Expected multipart/form-data with a 'file'.")

    upload = form.get("file")
    # Starlette UploadFile exposes filename/content_type/read; a bare string is
    # a form field, not a file.
    if upload is None or not hasattr(upload, "read"):
        raise ValidationError(
            message="A 'file' part is required.", details={"field": "file"}
        )

    mime = getattr(upload, "content_type", None) or "application/octet-stream"
    if not _mime_allowed(mime):
        raise ValidationError(
            message=f"Unsupported file type: {mime}",
            details={"mime_type": mime},
        )

    content = await upload.read()
    size = len(content)
    if size == 0:
        raise ValidationError(message="Uploaded file is empty.")
    if size > DEFAULT_MAX_UPLOAD_ITEM_BYTES:
        raise ValidationError(
            message="File exceeds the maximum allowed size.",
            details={"max_bytes": DEFAULT_MAX_UPLOAD_ITEM_BYTES, "size": size},
        )

    filename = _safe_filename(getattr(upload, "filename", "") or "upload")
    path = f"messenger_uploads/{agent_id}/{session_id}/{uuid.uuid4().hex}_{filename}"

    from jvagent.core.app import App

    app = await App.get()
    saved = await app.save_file(path, content, metadata={"mime": mime})
    if not saved:
        raise ValidationError(
            message="File storage is unavailable; upload was not saved.",
            details={"reason": "storage_unavailable"},
        )

    rel_url = await app.get_file_url(path)
    base = get_public_base_url()
    if base and rel_url and rel_url.startswith("/"):
        url = base.rstrip("/") + rel_url
    else:
        url = rel_url or ""

    return {
        "url": url,
        "mime_type": mime,
        "filename": filename,
        "size": size,
    }
