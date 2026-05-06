"""In-process file I/O scoped to ``<agent_id>/<user_id>/`` under jvspatial storage.

Exposes strict storage helpers for LLM tool modules and
``*_with_local_fallback`` helpers for imperative skill code when App/storage is
unavailable (e.g. tests). Does not spawn MCP subprocesses.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.storage.security import PathSanitizer

from jvagent.action.mcp.sandbox import (
    absolute_under_files_root,
    effective_user_segment,
    is_local_file_interface,
    provision_sandbox_dir,
    resolve_mcp_sandbox_relpath,
    resolve_sandbox_root,
)
from jvagent.core.app import App

logger = logging.getLogger(__name__)

# Common first path segments for artifacts; entire sandbox remains writable.
_PREFERRED_WRITE_ROOTS: Tuple[str, ...] = (
    "output",
    "outputs",
    "drafts",
    "uploads",
    "workspace",
    "artifacts",
    "tmp",
    "temp",
)


def _parse_listing_lines(listing: str) -> Tuple[List[str], List[str]]:
    dirs: List[str] = []
    files: List[str] = []
    for raw in (listing or "").splitlines():
        line = raw.strip()
        if line.startswith("[DIR]  "):
            dirs.append(line[7:].strip())
        elif line.startswith("[FILE] "):
            files.append(line[6:].strip())
    return sorted(dirs), sorted(files)


def validate_relative_write_path(path: str) -> str:
    """Normalize *path* for writes or raise if it is not a safe sandbox-relative path."""
    p = (path or "").strip().replace("\\", "/")
    if not p or p == ".":
        raise ValueError(
            "path must be a non-empty relative path (e.g. output/doc.md), not '.' or empty"
        )
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise ValueError(
            "path must be relative to the sandbox, not an absolute filesystem path"
        )
    segments = [s for s in p.split("/") if s and s != "."]
    if any(s == ".." for s in segments):
        raise ValueError("path must not contain '..' segments")
    return p


def normalize_sandbox_dir_prefix(raw: Optional[str], *, default: str = "output") -> str:
    """Return a safe sandbox-relative directory prefix (no leading ./, .., or absolutes)."""
    p = (raw or "").strip().replace("\\", "/")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise ValueError(
            "output directory must be sandbox-relative, not an absolute filesystem path"
        )
    segments = [s for s in p.split("/") if s and s != "."]
    joined = "/".join(segments) if segments else default
    validate_relative_write_path(f"{joined}/.jvagent_dir_check")
    return joined


async def describe_write_workspace(visitor: Any) -> Dict[str, Any]:
    """Summarize top-level sandbox content and suggested write prefixes for the LLM."""
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    listing = await list_directory(visitor, "")
    dirs, files = _parse_listing_lines(listing)
    prefixes: List[str] = []
    seen: set[str] = set()
    for d in dirs:
        if d not in seen:
            seen.add(d)
            prefixes.append(d)
    for pref in _PREFERRED_WRITE_ROOTS:
        if pref not in seen:
            seen.add(pref)
            prefixes.append(pref)
    prefixes.append(".")
    return {
        "sandbox_scope": (
            "All paths for write_file and related tools are relative to this "
            "user's workspace root (not the host filesystem)."
        ),
        "recommended_relative_prefixes": prefixes,
        "existing_top_level_directories": dirs,
        "existing_top_level_files": files,
        "listing": listing,
    }


async def resolve_agent_user(visitor: Any) -> Tuple[str, str]:
    """Return ``(agent_id, user_id_segment)`` for sandbox path construction.

    The user segment follows ``effective_user_segment``:
    ``visitor.user_id`` when authenticated, otherwise ``visitor.session_id``
    (per-session sandbox for anonymous callers), otherwise the
    ``MCP_FILESYSTEM_SANDBOX_DEFAULT_USER`` sentinel (default ``_default``).
    Sanitization is applied downstream by ``resolve_mcp_sandbox_relpath``.
    """
    agent = getattr(visitor, "_agent", None)
    raw_agent_id = ""
    if agent is not None:
        raw_agent_id = str(getattr(agent, "id", "") or "").strip()
    if not raw_agent_id:
        raw_agent_id = "unknown"

    default_seg = (
        os.getenv("MCP_FILESYSTEM_SANDBOX_DEFAULT_USER") or "_default"
    ).strip() or "_default"
    user_id = effective_user_segment(
        getattr(visitor, "user_id", None),
        getattr(visitor, "session_id", None),
        default=default_seg,
    )
    return raw_agent_id, user_id


def _join_key_parts(*parts: str) -> str:
    rel = "/".join(p.strip("/").replace("\\", "/") for p in parts if p)
    rel = rel.replace("//", "/")
    if not rel:
        return rel
    try:
        return PathSanitizer.sanitize_path(rel)
    except Exception:
        return rel.replace("..", "").lstrip("/")


def storage_key_for_path(agent_id: str, user_id: str, user_path: str) -> str:
    """Full storage key: ``<sanitized_agent>/<sanitized_user>/<user_path>``."""
    base = resolve_mcp_sandbox_relpath(agent_id, user_id)
    up = (user_path or ".").replace("\\", "/").lstrip("/")
    if not up or up == ".":
        return _join_key_parts(base) if base else base
    return _join_key_parts(base, up)


async def _get_file_interface(visitor: Any) -> Any:
    app = await App.get()
    if not app:
        raise RuntimeError("App is not available; cannot access file storage")
    if not getattr(app, "file_storage_enabled", True):
        raise RuntimeError("File storage is disabled for this app")
    return await app.get_file_interface()


async def _provision_rel_prefix(visitor: Any, agent_id: str, user_id: str) -> None:
    rel = resolve_mcp_sandbox_relpath(agent_id, user_id)
    try:
        fi = await _get_file_interface(visitor)
    except Exception as e:
        logger.debug("fileinterface provision skip: %s", e)
        return
    app = await App.get()
    if not app or not getattr(app, "file_storage_enabled", True):
        return
    try:
        if is_local_file_interface(fi):
            files_root = resolve_sandbox_root(
                (os.getenv("MCP_FILESYSTEM_SANDBOX_ROOT") or "").strip()
            )
            abs_p = absolute_under_files_root(files_root, rel)
            p = abs_p
        else:
            p = rel.replace("\\", "/")
        await provision_sandbox_dir(p, fi)
    except Exception as e:
        logger.debug("fileinterface provision: %s", e)


async def read_text_file(
    visitor: Any,
    path: str,
    *,
    head: Optional[int] = None,
    tail: Optional[int] = None,
) -> str:
    """Read UTF-8 text from *path* (relative to user sandbox)."""
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    data = await fi.get_file(key)
    if data is None:
        raise FileNotFoundError(f"File not found: {path!r} (key={key!r})")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if head is not None and head > 0:
        return "".join(lines[:head])
    if tail is not None and tail > 0:
        return "".join(lines[-tail:])
    return text


async def write_text_file(visitor: Any, path: str, content: str) -> None:
    """Write UTF-8 text to *path* (relative to user sandbox)."""
    path = validate_relative_write_path(path)
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    await fi.save_file(key, content.encode("utf-8"))


async def write_binary_file(visitor: Any, path: str, data: bytes) -> None:
    """Write binary content to *path* (relative to user sandbox)."""
    path = validate_relative_write_path(path)
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    await fi.save_file(key, data)


async def copy_host_file_into_sandbox(
    visitor: Any, source_local_path: str, dest_sandbox_path: str
) -> None:
    """Read a host filesystem file and write its bytes to *dest_sandbox_path* (strict).

    Does not fall back to writing *dest_sandbox_path* on the host if storage fails.
    """
    dest_sandbox_path = validate_relative_write_path(dest_sandbox_path)
    parent = str(Path(dest_sandbox_path).parent).replace("\\", "/")
    if parent and parent != ".":
        await create_directory(visitor, parent)
    with open(source_local_path, "rb") as f:
        data = f.read()
    await write_binary_file(visitor, dest_sandbox_path, data)


async def read_binary_file(visitor: Any, path: str) -> Optional[bytes]:
    """Read raw bytes from *path* (relative to user sandbox).

    Returns ``None`` if the object does not exist. Raises if storage is
    unavailable (same as other strict helpers).
    """
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    return await fi.get_file(key)


async def create_directory(visitor: Any, path: str) -> None:
    """Ensure a directory exists (marker for object storage, makedirs for local)."""
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    k = storage_key_for_path(agent_id, user_id, path)
    marker = f"{k.rstrip('/')}/.jvdirectory" if k else ".jvdirectory"
    if not await fi.file_exists(marker):
        await fi.save_file(marker, b"", metadata={"type": "directory"})


async def list_directory(visitor: Any, path: str) -> str:
    """Human-readable listing under *path* (prefix listing)."""
    agent_id, user_id = await resolve_agent_user(visitor)
    await _provision_rel_prefix(visitor, agent_id, user_id)
    fi = await _get_file_interface(visitor)
    k = storage_key_for_path(agent_id, user_id, path)
    prefix = f"{k}/" if k and not k.endswith("/") else (k or "")
    items = await fi.list_files(prefix=prefix, max_results=500)
    if not items:
        return "(empty)"
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        p = str(it.get("path", ""))
        if prefix and not p.startswith(prefix) and p != prefix:
            continue
        rest = p[len(prefix) :].lstrip("/") if prefix else p
        if not rest:
            continue
        if "/" in rest:
            part = rest.split("/")[0]
            line = f"[DIR]  {part}"
        else:
            part = rest
            line = f"[FILE] {part}"
        if part not in seen:
            seen.add(part)
            out.append(line)
    return "\n".join(sorted(out)) if out else "(empty)"


async def delete_file(visitor: Any, path: str) -> bool:
    """Delete a file at *path* (relative to user sandbox)."""
    agent_id, user_id = await resolve_agent_user(visitor)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    return await fi.delete_file(key)


async def file_exists(visitor: Any, path: str) -> bool:
    """Return True if *path* exists under the user sandbox."""
    agent_id, user_id = await resolve_agent_user(visitor)
    fi = await _get_file_interface(visitor)
    key = storage_key_for_path(agent_id, user_id, path)
    return await fi.file_exists(key)


async def write_text_file_with_local_fallback(
    visitor: Any, path: str, content: str
) -> None:
    """Write UTF-8 text via storage, or ``Path.write_text`` if that fails."""
    try:
        await write_text_file(visitor, path, content)
    except Exception as exc:
        logger.debug(
            "fileinterface write_text_file_with_local_fallback: %s; using local",
            exc,
        )
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


async def create_directory_with_local_fallback(visitor: Any, path: str) -> None:
    """Create directory via storage, or ``Path.mkdir`` if that fails."""
    try:
        await create_directory(visitor, path)
    except Exception as exc:
        logger.debug(
            "fileinterface create_directory_with_local_fallback: %s; using local",
            exc,
        )
        Path(path).mkdir(parents=True, exist_ok=True)


async def copy_binary_file_with_local_fallback(
    visitor: Any,
    source_local_path: str,
    dest_sandbox_path: str,
) -> None:
    """Copy host file bytes into storage, or ``shutil.copy2`` to *dest_sandbox_path*."""
    try:
        parent = str(Path(dest_sandbox_path).parent).replace("\\", "/")
        if parent and parent != ".":
            try:
                await create_directory(visitor, parent)
            except Exception:
                pass
        with open(source_local_path, "rb") as f:
            data = f.read()
        await write_binary_file(visitor, dest_sandbox_path, data)
    except Exception as exc:
        logger.debug(
            "fileinterface copy_binary_file_with_local_fallback: %s; using local",
            exc,
        )
        p = Path(dest_sandbox_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_local_path, str(p))
