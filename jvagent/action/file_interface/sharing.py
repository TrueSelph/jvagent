"""Bounded artifact snapshots over the existing storage and proxy services."""

import hashlib
import re
from datetime import timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from jvspatial import flush_deferred_entities
from jvspatial.api.constants import APIRoutes
from jvspatial.api.context import get_current_server
from jvspatial.storage.interfaces.local import LocalFileInterface

from jvagent.action.file_interface import _core
from jvagent.core.app import App
from jvagent.core.public_url import get_public_base_url
from jvagent.logging.capabilities import install_download_log_filters
from jvagent.tooling.context import require_tool_visitor

VERSION = "native-file-share-v1"
MAX_BYTES = 8 * 1024 * 1024


async def _bounded_bytes(fi: Any, path: str, limit: int) -> bytes:
    stream = fi.stream_file(path, chunk_size=min(8192, limit + 1))
    data = bytearray()
    try:
        async for chunk in stream:
            if len(data) + len(chunk) > limit:
                raise ValueError("Artifact exceeds the host byte limit")
            data.extend(chunk)
    finally:
        await stream.aclose()
    return bytes(data)


async def share_file(
    action: Any, visitor: Any, path: str, expires_in: int, limit: int
) -> dict[str, Any]:
    """Create a separately stored snapshot; SDK mutation intent precedes I/O."""
    if visitor is not require_tool_visitor():
        raise ValueError("Artifact sharing requires the admitted host visitor")
    install_download_log_filters()
    if (
        isinstance(expires_in, bool)
        or not isinstance(expires_in, int)
        or not 1 <= expires_in <= 3600
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_BYTES
    ):
        raise ValueError("Invalid artifact byte or expiry limit")
    base = get_public_base_url().rstrip("/")
    url = urlsplit(base)
    if (
        url.scheme not in {"http", "https"}
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Configure a trusted JVAGENT_PUBLIC_BASE_URL")
    server = get_current_server()
    if server is None or server.config.file_storage.file_storage_enabled:
        raise ValueError(
            "An admitted server with generic storage HTTP routes disabled is required"
        )
    path = _core.validate_relative_path(path)
    agent_id, user_id = await _core.resolve_agent_user(visitor)
    if action.agent_id != agent_id or not action.enabled:
        raise ValueError("Artifact action ownership changed")
    app = await App.get()
    fi = await _core._get_file_interface(visitor)
    if app is None or not isinstance(fi, LocalFileInterface):
        raise ValueError("Only local artifact storage is qualified")
    scoped = _core._local_user_storage(fi, agent_id, user_id, path)
    content = await _bounded_bytes(scoped, path, limit)
    manager = await app.get_proxy_manager()
    if manager is None:
        raise ValueError("Temporary file proxy service is unavailable")
    filename = PurePosixPath(path).name
    key = f"native_exports/{action.id}/{uuid4().hex}/{filename}"
    # This namespace is outside every script/user mount. Later edits to the
    # original cannot change an already-issued artifact snapshot.
    await fi.save_file(key, content)
    digest = hashlib.sha256(content).hexdigest()
    if hashlib.sha256(await _bounded_bytes(fi, key, limit)).hexdigest() != digest:
        raise RuntimeError("Artifact snapshot acknowledgement mismatch")
    proxy = await manager.create_proxy(
        file_path=key,
        expires_in=expires_in,
        one_time=False,
        code_length=24,
        metadata={
            "version": VERSION,
            "action_id": action.id,
            "agent_id": agent_id,
            "user_id": user_id,
            "snapshot_path": key,
            "filename": filename,
            "bytes": len(content),
            "sha256": digest,
        },
    )
    await flush_deferred_entities(proxy, strict=True)
    require_tool_visitor()
    return {
        "ok": True,
        "path": path,
        "filename": filename,
        "bytes": len(content),
        "sha256": digest,
        "expires_at": proxy.expires_at.astimezone(timezone.utc).isoformat(),
        "url": f"{base}{APIRoutes.PREFIX}/actions/{quote(action.id, safe='')}/downloads/{proxy.code}",
    }


async def download_file(action: Any, code: str) -> Any:
    """Resolve the existing capability and return only verified snapshot bytes."""
    from fastapi import HTTPException, Response
    from jvspatial.storage.exceptions import StorageError

    install_download_log_filters()
    if not action.enabled or re.fullmatch(r"[A-Za-z0-9_-]{32}", code) is None:
        raise HTTPException(404, "Artifact unavailable")
    app = await App.get()
    manager = await app.get_proxy_manager() if app else None
    if manager is None:
        raise HTTPException(404, "Artifact unavailable")
    try:
        path, metadata = await manager.resolve_proxy(code)
    except (StorageError, FileNotFoundError):
        raise HTTPException(404, "Artifact unavailable") from None
    prefix = f"native_exports/{action.id}/"
    if (
        not isinstance(path, str)
        or not isinstance(metadata, dict)
        or (
            metadata.get("version") != VERSION
            or metadata.get("action_id") != action.id
            or metadata.get("agent_id") != action.agent_id
            or metadata.get("snapshot_path") != path
            or not path.startswith(prefix)
        )
    ):
        raise HTTPException(404, "Artifact unavailable")
    fi = await app.get_file_interface()
    if not isinstance(fi, LocalFileInterface):
        raise HTTPException(404, "Artifact unavailable")
    # Bind the recorded digest to the bytes delivered, never a mutable user path.
    try:
        if _core.validate_relative_path(path) != path:
            raise ValueError("Invalid snapshot path")
        data = await _bounded_bytes(fi, path, MAX_BYTES)
    except (ValueError, OSError, StorageError):
        raise HTTPException(404, "Artifact unavailable") from None
    if len(data) != metadata.get("bytes") or hashlib.sha256(
        data
    ).hexdigest() != metadata.get("sha256"):
        raise HTTPException(404, "Artifact unavailable")
    response = Response(data, media_type="application/octet-stream")
    filename = quote(str(metadata.get("filename", "artifact")), safe="")
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response
