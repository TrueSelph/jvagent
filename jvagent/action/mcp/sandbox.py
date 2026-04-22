"""MCP filesystem sandbox: resolve roots and paths under jvspatial file storage."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from jvspatial.env import env, resolve_file_storage_root

logger = logging.getLogger(__name__)

# Safe segment for user_id / agent parts in storage keys and local paths
_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9_.\-@]+")


def resolve_sandbox_root(configured: str = "") -> str:
    """Resolve the filesystem root used for MCP sandbox directories.

    Priority:
    1. ``JVSPATIAL_FILES_ROOT_PATH`` (jvspatial default for stored files)
    2. Non-empty ``configured`` (YAML / env ``MCP_FILESYSTEM_SANDBOX_ROOT``)
    3. ``resolve_file_storage_root()`` (``./.files`` or ``/tmp/.files`` in serverless)
    """
    files = env("JVSPATIAL_FILES_ROOT_PATH")
    if files and str(files).strip():
        return str(files).strip()
    cfg = (configured or "").strip()
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    return os.path.abspath(resolve_file_storage_root())


def sanitize_segment(segment: str, *, default: str = "anonymous") -> str:
    """Make a single path component safe for storage keys and directory names."""
    s = (segment or "").strip()
    if not s:
        return default
    s = _SAFE_SEGMENT.sub("_", s)
    s = s.strip("._")
    return s[:200] if s else default


def resolve_mcp_sandbox_relpath(agent_id: str, user_id: str) -> str:
    """Relative key under the file storage root: ``<agentId>/<userId>``.

    Both segments are sanitized for safe directory/object names. The graph
    ``agent_id`` and auth ``user_id`` are the logical names; slashes and
    other unsafe characters are normalized (e.g. ``namespace/name`` → one segment).
    """
    aid = sanitize_segment(agent_id, default="agent")
    uid = sanitize_segment(user_id, default="anonymous")
    return f"{aid}/{uid}".replace("\\", "/")


def is_local_file_interface(file_interface: Any) -> bool:
    """True if storage is process-local disk (``LocalFileInterface``)."""
    name = type(file_interface).__name__
    return "Local" in name or name == "LocalFileInterface"


def _is_local_provider(file_interface: Any) -> bool:
    return is_local_file_interface(file_interface)


async def provision_sandbox_dir(
    path: str, file_interface: Any, *, is_local_path: Optional[bool] = None
) -> None:
    """Ensure a sandbox directory exists.

    For local storage, ``path`` should be an absolute directory path on disk.
    For object storage, pass a relative key prefix (e.g. ``agents/ns/name``).

    If ``is_local_path`` is None, it is inferred from the storage interface class.
    """
    if not path:
        return
    local = (
        is_local_path
        if is_local_path is not None
        else _is_local_provider(file_interface)
    )
    if local:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            logger.warning("provision_sandbox_dir: makedirs failed for %s: %s", path, e)
        return
    # S3 / non-local: optional marker object (path is a storage key prefix)
    key = path.replace("\\", "/").strip("/")
    if not key:
        return
    marker = f"{key}/.jvagent_sandbox"
    try:
        exists = await file_interface.file_exists(marker)
        if not exists:
            await file_interface.save_file(marker, b"", metadata={"sandbox": "1"})
    except Exception as e:
        logger.debug("provision_sandbox_dir: marker for %s: %s", marker, e)


def should_use_jvspatial_fs_server() -> bool:
    """Use Python stdio MCP server backed by FileStorageInterface when storage is not local."""
    prov = (env("JVSPATIAL_FILE_STORAGE_PROVIDER") or "local").strip().lower()
    return prov == "s3"


def absolute_under_files_root(files_root: str, relative: str) -> str:
    """Join sandbox root and a relative key; normalize for local process roots."""
    root = os.path.abspath(os.path.expanduser(files_root or "."))
    rel = (relative or "").replace("\\", "/").strip("/")
    return os.path.normpath(os.path.join(root, *rel.split("/"))) if rel else root
