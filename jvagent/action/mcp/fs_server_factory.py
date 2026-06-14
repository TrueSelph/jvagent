"""Build MCP stdio clients for filesystem servers (jvspatial-backed vs npx)."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.env import env

from jvagent.action.mcp.client import MCPClientWrapper

_JVFS_MODULE = "jvagent.action.mcp.jvspatial_fs_server"


def should_use_jvspatial_fs_server() -> bool:
    """Prefer Python stdio MCP server backed by FileStorageInterface when not using local disk.

    When ``JVSPATIAL_FILE_STORAGE_PROVIDER`` is ``s3``, the Node ``npx`` filesystem
    server cannot access the bucket; use :mod:`jvagent.action.mcp.jvspatial_fs_server`
    instead. For ``local`` storage, ``npx`` + ``@modelcontextprotocol/server-filesystem``
    is the default unless ``type: jvspatial_fs`` is set explicitly.
    """
    prov = (env("JVSPATIAL_FILE_STORAGE_PROVIDER") or "local").strip().lower()
    return prov == "s3"


def use_jvfs_for_sandboxed_fs(raw: Dict[str, Any], sandbox_mode: bool) -> bool:
    """Whether to spawn ``jvspatial_fs_server`` for this server entry."""
    if not sandbox_mode:
        return False
    t = str(raw.get("type") or "").strip().lower()
    if t in ("jvspatial_fs", "jvfs", "jvspatial"):
        return True
    if t in ("npx_filesystem", "npx"):
        return False
    return should_use_jvspatial_fs_server()


def strip_trailing_path_arg(args: List[str]) -> List[str]:
    """Remove a trailing local filesystem root from MCP filesystem server args."""
    if not args:
        return args
    last = str(args[-1]).strip()
    if not last:
        return list(args)
    if last in (".", "..") or last.startswith(("/", "./", "../", "~")):
        return list(args[:-1])
    if len(last) > 1 and last[0].isalpha() and last[1] == ":":  # Windows "C:\..."
        return list(args[:-1])
    if last.startswith("@"):
        return list(args)
    if "/" in last or (os.path.sep in last and len(last) < 512):
        return list(args[:-1])
    return list(args)


def is_filesystem_mcp_server(raw: Dict[str, Any], use_jvfs: bool) -> bool:
    if use_jvfs:
        return True
    args = raw.get("args") or []
    if not isinstance(args, (list, tuple)):
        return False
    s = " ".join(str(x) for x in args)
    return "server-filesystem" in s or "modelcontextprotocol/server-filesystem" in s


def build_jvfs_client(
    rel_prefix: str, connect_t: float, call_t: float
) -> Tuple[str, List[str], MCPClientWrapper]:
    rel = (rel_prefix or "").replace("\\", "/").strip().strip("/")
    cmd = sys.executable
    args: List[str] = ["-m", _JVFS_MODULE, "--root-prefix", rel]
    return (
        cmd,
        args,
        MCPClientWrapper(
            "stdio",
            command=cmd,
            args=args,
            env=None,
            url="",
            connect_timeout=connect_t,
            call_timeout=call_t,
        ),
    )


def build_npx_filesystem_client(
    npx_cmd: str,
    base_args: List[str],
    absolute_root: str,
    connect_t: float,
    call_t: float,
    env: Optional[Dict[str, str]],
) -> Tuple[str, List[str], MCPClientWrapper]:
    a = strip_trailing_path_arg(list(base_args))
    a = list(a) + [absolute_root]
    cmd = (npx_cmd or "npx").strip() or "npx"
    return (
        cmd,
        a,
        MCPClientWrapper(
            "stdio",
            command=cmd,
            args=a,
            env=env,
            url="",
            connect_timeout=connect_t,
            call_timeout=call_t,
        ),
    )
