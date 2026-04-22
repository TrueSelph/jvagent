"""Stdio MCP filesystem server backed by jvspatial ``FileStorageInterface`` (local or S3).

Run as: ``python -m jvagent.action.mcp.jvspatial_fs_server --root-prefix <key>``

Requires Python 3.10+ and the ``mcp`` package (same as :class:`MCPAction` / :class:`MCPClientWrapper`).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import sys
from typing import Any, List, Optional, Sequence

from jvspatial.env import env, resolve_file_storage_root
from jvspatial.storage import create_storage
from jvspatial.storage.security import PathSanitizer

logger = logging.getLogger("jvagent.jvfs")

_PREFIX: str = ""
_st: Any = None


def _get_storage() -> Any:
    """Build storage the same way as :meth:`jvagent.core.app.App.get_file_interface`."""
    global _st
    if _st is not None:
        return _st
    provider = (env("JVSPATIAL_FILE_STORAGE_PROVIDER", default="") or "local").strip()
    if provider == "local":
        root = resolve_file_storage_root()
        _st = create_storage(provider="local", root_dir=root)
    elif provider == "s3":
        _st = create_storage(
            provider="s3",
            bucket_name=env("JVSPATIAL_S3_BUCKET_NAME", default=""),
            region_name=env("JVSPATIAL_S3_REGION", default="us-east-1"),
            access_key_id=env("JVSPATIAL_S3_ACCESS_KEY", default="") or None,
            secret_access_key=env("JVSPATIAL_S3_SECRET_KEY", default="") or None,
            endpoint_url=env("JVSPATIAL_S3_ENDPOINT_URL", default="") or None,
        )
    else:
        _st = create_storage(provider=provider, root_dir=resolve_file_storage_root())
    return _st


def _join_key(*parts: str) -> str:
    rel = "/".join(p.strip("/").replace("\\", "/") for p in parts if p)
    rel = rel.replace("//", "/")
    try:
        return PathSanitizer.sanitize_path(rel) if rel else rel
    except Exception:
        if not rel:
            return rel
        return rel.replace("..", "").lstrip("/")


def _full_key(user_path: str) -> str:
    user_path = (user_path or ".").replace("\\", "/").lstrip("/")
    base = _PREFIX.strip().strip("/")
    if not user_path or user_path == ".":
        return _join_key(base) if base else base
    return _join_key(base, user_path) if base else _join_key(user_path)


def _err(msg: str) -> str:
    return f"Error: {msg}"


async def _read_text_file(
    path: str, head: Optional[int] = None, tail: Optional[int] = None
) -> str:
    st = _get_storage()
    k = _full_key(path)
    data = await st.get_file(k)  # type: ignore[union-attr]
    if data is None:
        return _err("file not found")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    if head is not None and head > 0:
        return "".join(lines[:head])
    if tail is not None and tail > 0:
        return "".join(lines[-tail:])
    return text if text else ""


async def _write_file(path: str, content: str) -> str:
    st = _get_storage()
    k = _full_key(path)
    res = await st.save_file(k, content.encode("utf-8"))
    return f"Wrote {k} (size={res.get('size', len(content)) if isinstance(res, dict) else len(content)})"


async def _create_directory(path: str) -> str:
    st = _get_storage()
    k = _full_key(path)
    marker = f"{k.rstrip('/')}/.jvdirectory" if k else ".jvdirectory"
    if not await st.file_exists(marker):
        await st.save_file(marker, b"", metadata={"type": "directory"})
    return f"Directory ensured at {k or '.'}"


async def _list_directory(path: str) -> str:
    st = _get_storage()
    k = _full_key(path)
    prefix = f"{k}/" if k and not k.endswith("/") else (k or "")
    items: List[dict] = await st.list_files(prefix=prefix, max_results=500)  # type: ignore[union-attr]
    if not items:
        return "(empty)"
    out: List[str] = []
    seen: set = set()
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


async def _move_file(source: str, destination: str) -> str:
    st = _get_storage()
    s = _full_key(source)
    d = _full_key(destination)
    b = await st.get_file(s)
    if b is None:
        return _err("source not found")
    await st.save_file(d, b)
    await st.delete_file(s)
    return f"Moved to {d}"


async def _get_file_info(path: str) -> str:
    st = _get_storage()
    k = _full_key(path)
    meta = await st.get_metadata(k)  # type: ignore[union-attr]
    if not meta:
        return _err("not found or no metadata")
    return json.dumps(meta, default=str)[:8000]


async def _search_files(path: str, pattern: str) -> str:
    st = _get_storage()
    k = _full_key(path)
    prefix = f"{k}/" if k and not k.endswith("/") else (k or "")
    items: List[dict] = await st.list_files(prefix=prefix, max_results=2000)  # type: ignore[union-attr]
    matches = [
        it.get("path", "")
        for it in items
        if pattern and fnmatch.fnmatch(os.path.basename(it.get("path", "")), pattern)
    ]
    return "\n".join(m for m in matches if m) or "(no matches)"


async def _list_allowed_directories() -> str:
    return f"[ALLOWED] {_PREFIX or '(storage root)'}"


# --- FastMCP wiring ---


def _run_fastmcp() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("jvspatial-filesystem")

    @mcp.tool(
        description="Read a text file under the sandbox. Optional head/tail line limits."
    )
    async def read_text_file(
        path: str, head: Optional[int] = None, tail: Optional[int] = None
    ) -> str:
        return await _read_text_file(path, head=head, tail=tail)

    @mcp.tool(description="Create or update a file with UTF-8 text content.")
    async def write_file(path: str, content: str) -> str:
        return await _write_file(path, content)

    @mcp.tool(description="Create a directory (storage-backed marker for S3).")
    async def create_directory(path: str) -> str:
        return await _create_directory(path)

    @mcp.tool(description="List files in a directory (prefix listing).")
    async def list_directory(path: str) -> str:
        return await _list_directory(path)

    @mcp.tool(description="Move or rename a file within storage.")
    async def move_file(source: str, destination: str) -> str:
        return await _move_file(source, destination)

    @mcp.tool(description="Return JSON metadata for a path if available.")
    async def get_file_info(path: str) -> str:
        return await _get_file_info(path)

    @mcp.tool(
        description="Search files by glob pattern (basename) under a path prefix."
    )
    async def search_files(
        path: str,
        pattern: str,
    ) -> str:
        return await _search_files(path, pattern)

    @mcp.tool(description="List the logical allowed directory for this server.")
    async def list_allowed_directories() -> str:
        return await _list_allowed_directories()

    mcp.run()


def _parse() -> str:
    p = argparse.ArgumentParser(description="jvspatial MCP filesystem (stdio)")
    p.add_argument(
        "--root-prefix",
        default="",
        help="Key prefix under the file storage root (e.g. agents/ns/name/workspace)",
    )
    a = p.parse_args()
    return (a.root_prefix or "").replace("\\", "/").strip().strip("/")


def main(argv: Optional[Sequence[str]] = None) -> None:
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    global _PREFIX
    _PREFIX = _parse()
    try:
        _run_fastmcp()
    except ImportError as e:
        logger.error("MCP / FastMCP not available: %s", e)
        print(
            "Error: the 'mcp' package (Python 3.10+) is required for jvspatial_fs_server.",
            file=sys.stderr,
        )
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
