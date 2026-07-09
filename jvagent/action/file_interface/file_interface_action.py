"""File interface action.

Exposes sandboxed, per-user file I/O as first-class orchestrator tools
(ADR-0012: actions are first-class tools). Each tool resolves the live walker
from the dispatch context and delegates to the strict storage helpers in
:mod:`jvagent.action.file_interface._core`, which validate every relative path
so a tool cannot escape the ``<agent_id>/<user_id>/`` sandbox.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Annotated, Optional

from jvagent.action.base import Action
from jvagent.action.file_interface import _core
from jvagent.tooling.tool_decorator import tool
from jvagent.tooling.tool_executor import get_tool_visitor

logger = logging.getLogger(__name__)

_NO_CONTEXT = "(no execution context)"


class FileInterfaceAction(Action):
    """Sandboxed per-user file I/O tools backed by the jvspatial file interface."""

    @tool(name="file_interface__describe_write_workspace")
    async def _t_describe_write_workspace(self) -> str:
        """First step for file interface work in a task. Returns top-level directories and files, recommended relative path prefixes, and what is writable under the user sandbox. Does not read or write user file content."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        try:
            data = await _core.describe_write_workspace(visitor)
            return json.dumps({"ok": True, **data}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__read_file")
    async def _t_read_file(
        self,
        path: Annotated[
            str, "Relative path under the user sandbox (e.g. output/doc.md)."
        ],
        head: Annotated[Optional[int], "If set, return only the first N lines."] = None,
        tail: Annotated[Optional[int], "If set, return only the last N lines."] = None,
    ) -> str:
        """Read a text file from the current agent's user-scoped storage (jvspatial file interface: local or S3). Path is relative to the sandbox root. Optional head/tail limit lines returned. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        if head is not None:
            try:
                head = int(head)
            except (TypeError, ValueError):
                head = None
        if tail is not None:
            try:
                tail = int(tail)
            except (TypeError, ValueError):
                tail = None
        try:
            text = await _core.read_text_file(visitor, path, head=head, tail=tail)
            return json.dumps({"ok": True, "path": path, "content": text}, indent=2)
        except FileNotFoundError as e:
            return json.dumps({"ok": False, "error": str(e)})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__write_file")
    async def _t_write_file(
        self,
        path: Annotated[str, "Relative path (e.g. output/notes.md)."],
        content: Annotated[str, "Full file content as UTF-8 text."],
    ) -> str:
        """Create or overwrite a file with UTF-8 text in the current user's sandbox (jvspatial storage). Parent path segments are created as needed. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        content = str(content or "")
        try:
            await _core.write_text_file(visitor, path, content)
            return json.dumps(
                {
                    "ok": True,
                    "path": path,
                    "bytes": len(content.encode("utf-8")),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__write_binary_file")
    async def _t_write_binary_file(
        self,
        path: Annotated[str, "Relative path (e.g. output/report.pdf)."],
        content_b64: Annotated[str, "File bytes as a standard base64 string."],
    ) -> str:
        """Write binary data to a path in the user sandbox. Pass content as base64-encoded ASCII (standard for JSON tools). Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        b64 = str(content_b64 or "")
        try:
            data = base64.b64decode(b64)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"base64 decode failed: {e}"})
        try:
            await _core.write_binary_file(visitor, path, data)
            return json.dumps({"ok": True, "path": path, "size": len(data)}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__list_directory")
    async def _t_list_directory(
        self,
        path: Annotated[
            str, "Directory path relative to sandbox (omit or '' for sandbox root)."
        ] = "",
    ) -> str:
        """List immediate files and subdirectories under a relative path in the user sandbox. Use path '' for the workspace root. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        try:
            listing = await _core.list_directory(visitor, path)
            return json.dumps(
                {"ok": True, "path": path or ".", "listing": listing}, indent=2
            )
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__create_directory")
    async def _t_create_directory(
        self,
        path: Annotated[str, "Relative directory path."],
    ) -> str:
        """Create a directory (or ensure it exists) under the user sandbox. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        try:
            await _core.create_directory(visitor, path)
            return json.dumps({"ok": True, "path": path}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__delete_file")
    async def _t_delete_file(
        self,
        path: Annotated[str, "Relative file path."],
    ) -> str:
        """Delete a file at a path relative to the user sandbox. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        try:
            ok = await _core.delete_file(visitor, path)
            return json.dumps({"ok": ok, "path": path}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

    @tool(name="file_interface__file_exists")
    async def _t_file_exists(
        self,
        path: Annotated[str, "Relative path."],
    ) -> str:
        """Return whether a file or marker exists at the given relative path. Follow the file interface workflow: call file_interface__describe_write_workspace first when starting file work in a task, then use this tool."""  # noqa: E501
        visitor = get_tool_visitor()
        if visitor is None:
            return _NO_CONTEXT
        path = str(path or "").strip()
        try:
            exists = await _core.file_exists(visitor, path)
            return json.dumps({"ok": True, "path": path, "exists": exists}, indent=2)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
