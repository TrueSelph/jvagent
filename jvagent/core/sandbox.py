"""Per-user/agent filesystem sandbox service over jvspatial file storage.

This is the single home for jvagent's multitenant filesystem convention: every
caller's files live under ``<agent_id>/<user_id>/`` within the jvspatial file
storage root, and each user's slice is walled off from every other user's. It
is shared by all consumers of that convention:

- :class:`jvagent.action.mcp.mcp_action.MCPAction` — per-user filesystem MCP
  subprocess roots.
- :mod:`jvagent.action.file_interface._core` — in-process file I/O tools.
- :class:`jvagent.action.code_execution` — sandboxed code execution cwd.

It extends jvspatial's file interface (``resolve_file_storage_root`` + the
``file_interface`` provider) with the per-user segmenting, provisioning, and
path-safety primitives jvagent layers on top. Keeping it in core (rather than
under an action package) lets every consumer depend on one service instead of
reaching into a sibling action's submodule.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional, Tuple

from jvspatial.env import env, resolve_file_storage_root

logger = logging.getLogger(__name__)

# Safe segment for user_id / agent parts in storage keys and local paths
_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9_.\-@]+")


# --------------------------------------------------------------------------- #
# Root + segment resolution
# --------------------------------------------------------------------------- #
def resolve_sandbox_root(configured: str = "") -> str:
    """Resolve the filesystem root used for sandbox directories.

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


def sanitize_segment(segment: str, *, default: str = "_default") -> str:
    """Make a single path component safe for storage keys and directory names.

    Strips only leading/trailing dots (object-storage hostility, hidden-file
    semantics). Underscores are preserved so sentinel values like
    ``_default`` survive intact and remain distinguishable from real IDs.
    """
    s = (segment or "").strip()
    if not s:
        return default
    s = _SAFE_SEGMENT.sub("_", s)
    s = s.strip(".")
    return s[:200] if s else default


def effective_user_segment(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    *,
    default: str = "_default",
) -> str:
    """Resolve the per-user sandbox segment.

    Priority:
        1. ``user_id`` when set (authenticated caller).
        2. ``session_id`` when set (anonymous caller with a session — keeps
           that visitor's files isolated from other anonymous visitors).
        3. ``default`` (typically ``_default``) for system / no-context calls.

    The returned value is NOT sanitized; pass it to ``sanitize_segment`` (or
    ``resolve_user_sandbox_relpath``) which already handles unsafe characters.
    """
    uid = (user_id or "").strip()
    if uid:
        return uid
    sid = (session_id or "").strip()
    if sid:
        return sid
    return default


def resolve_user_sandbox_relpath(agent_id: str, user_id: str) -> str:
    """Relative key under the file storage root: ``<agentId>/<userId>``.

    Both segments are sanitized for safe directory/object names. The graph
    ``agent_id`` and auth ``user_id`` are the logical names; slashes and
    other unsafe characters are normalized (e.g. ``namespace/name`` → one segment).
    A real ``user_id`` must always be supplied; the ``_default`` fallback is
    reserved for system-level (no-user) sandbox provisioning only.
    """
    aid = sanitize_segment(agent_id, default="agent")
    uid = sanitize_segment(user_id, default="_default")
    return f"{aid}/{uid}".replace("\\", "/")


async def resolve_agent_user(visitor: Any) -> Tuple[str, str]:
    """Return ``(agent_id, user_id_segment)`` for sandbox path construction.

    The user segment follows ``effective_user_segment``: ``visitor.user_id``
    when authenticated, otherwise ``visitor.session_id`` (per-session sandbox
    for anonymous callers), otherwise the ``MCP_FILESYSTEM_SANDBOX_DEFAULT_USER``
    sentinel (default ``_default``). Sanitization is applied downstream by
    ``resolve_user_sandbox_relpath``. Async (no I/O today) to match the
    historical signature its callers ``await``.
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


# --------------------------------------------------------------------------- #
# Provider + provisioning
# --------------------------------------------------------------------------- #
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


def absolute_under_files_root(files_root: str, relative: str) -> str:
    """Join sandbox root and a relative key; normalize for local process roots."""
    root = os.path.abspath(os.path.expanduser(files_root or "."))
    rel = (relative or "").replace("\\", "/").strip("/")
    return os.path.normpath(os.path.join(root, *rel.split("/"))) if rel else root


async def provision_user_sandbox(
    agent_id: str,
    user_id: str,
    file_interface: Any,
    *,
    configured_root: str = "",
) -> str:
    """Provision the caller's per-user slice and return its location.

    For local storage, returns the absolute on-disk directory (created if
    absent) — usable directly as a subprocess cwd. For object storage, returns
    the relative key prefix (a sandbox marker is ensured). This is the single
    entry point a consumer needs to obtain a walled, ready-to-use per-user
    sandbox; it composes ``resolve_user_sandbox_relpath`` +
    ``resolve_sandbox_root`` + ``provision_sandbox_dir``.
    """
    rel = resolve_user_sandbox_relpath(agent_id, user_id)
    if is_local_file_interface(file_interface):
        root = resolve_sandbox_root(configured_root)
        path = absolute_under_files_root(root, rel)
        await provision_sandbox_dir(path, file_interface, is_local_path=True)
        return path
    await provision_sandbox_dir(rel, file_interface, is_local_path=False)
    return rel


# --------------------------------------------------------------------------- #
# Path safety (sandbox-relative)
# --------------------------------------------------------------------------- #
def validate_relative_path(path: str, *, allow_root: bool = False) -> str:
    """Normalize *path* or raise if it is not a safe sandbox-relative path.

    Rejects absolute filesystem paths, drive letters, and any ``..`` segment.
    Reads pass ``allow_root=True`` so the sandbox root (``""``/``.``) lists fine.
    """
    p = (path or "").strip().replace("\\", "/")
    if not p or p == ".":
        if allow_root:
            return ""
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
    return "/".join(segments)


def normalize_sandbox_dir_prefix(raw: Optional[str], *, default: str = "output") -> str:
    """Return a safe sandbox-relative directory prefix (no leading ./, .., or absolutes)."""
    p = (raw or "").strip().replace("\\", "/")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise ValueError(
            "output directory must be sandbox-relative, not an absolute filesystem path"
        )
    segments = [s for s in p.split("/") if s and s != "."]
    joined = "/".join(segments) if segments else default
    validate_relative_path(f"{joined}/.jvagent_dir_check")
    return joined


__all__ = [
    "resolve_sandbox_root",
    "sanitize_segment",
    "effective_user_segment",
    "resolve_user_sandbox_relpath",
    "resolve_agent_user",
    "is_local_file_interface",
    "provision_sandbox_dir",
    "absolute_under_files_root",
    "provision_user_sandbox",
    "validate_relative_path",
    "normalize_sandbox_dir_prefix",
]
