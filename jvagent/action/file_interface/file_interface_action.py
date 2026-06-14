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
from typing import Any, List, Optional

from jvagent.action.base import Action
from jvagent.action.file_interface import _core
from jvagent.tooling.tool_executor import get_dispatch_visitor

logger = logging.getLogger(__name__)

# Cross-reference appended to several tool descriptions (formerly OTHER_TOOLS).
_OTHER_TOOLS = (
    " Follow the file interface workflow: call file_interface__describe_write_workspace "
    "first when starting file work in a task, then use this tool."
)

_NO_CONTEXT = "(no execution context)"


class FileInterfaceAction(Action):
    """Sandboxed per-user file I/O tools backed by the jvspatial file interface."""

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        async def _describe_write_workspace() -> str:
            visitor = get_dispatch_visitor()
            if visitor is None:
                return _NO_CONTEXT
            try:
                data = await _core.describe_write_workspace(visitor)
                return json.dumps({"ok": True, **data}, indent=2)
            except Exception as e:
                return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

        async def _read_file(
            path: str, head: Optional[int] = None, tail: Optional[int] = None
        ) -> str:
            visitor = get_dispatch_visitor()
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

        async def _write_file(path: str, content: str) -> str:
            visitor = get_dispatch_visitor()
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

        async def _write_binary_file(path: str, content_b64: str) -> str:
            visitor = get_dispatch_visitor()
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
                return json.dumps(
                    {"ok": True, "path": path, "size": len(data)}, indent=2
                )
            except Exception as e:
                return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

        async def _list_directory(path: str = "") -> str:
            visitor = get_dispatch_visitor()
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

        async def _create_directory(path: str) -> str:
            visitor = get_dispatch_visitor()
            if visitor is None:
                return _NO_CONTEXT
            path = str(path or "").strip()
            try:
                await _core.create_directory(visitor, path)
                return json.dumps({"ok": True, "path": path}, indent=2)
            except Exception as e:
                return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

        async def _delete_file(path: str) -> str:
            visitor = get_dispatch_visitor()
            if visitor is None:
                return _NO_CONTEXT
            path = str(path or "").strip()
            try:
                ok = await _core.delete_file(visitor, path)
                return json.dumps({"ok": ok, "path": path}, indent=2)
            except Exception as e:
                return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

        async def _file_exists(path: str) -> str:
            visitor = get_dispatch_visitor()
            if visitor is None:
                return _NO_CONTEXT
            path = str(path or "").strip()
            try:
                exists = await _core.file_exists(visitor, path)
                return json.dumps(
                    {"ok": True, "path": path, "exists": exists}, indent=2
                )
            except Exception as e:
                return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})

        return [
            Tool(
                name="file_interface__describe_write_workspace",
                description=(
                    "First step for file interface work in a task. "
                    "Returns top-level directories and files, recommended relative "
                    "path prefixes, and what is writable under the user sandbox. "
                    "Does not read or write user file content."
                ),
                parameters_schema={"type": "object", "properties": {}},
                execute=_describe_write_workspace,
            ),
            Tool(
                name="file_interface__read_file",
                description=(
                    "Read a text file from the current agent's user-scoped storage "
                    "(jvspatial file interface: local or S3). Path is relative to the "
                    "sandbox root. Optional head/tail limit lines returned."
                    + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Relative path under the user sandbox "
                                "(e.g. output/doc.md)."
                            ),
                        },
                        "head": {
                            "type": "integer",
                            "description": "If set, return only the first N lines.",
                        },
                        "tail": {
                            "type": "integer",
                            "description": "If set, return only the last N lines.",
                        },
                    },
                    "required": ["path"],
                },
                execute=_read_file,
            ),
            Tool(
                name="file_interface__write_file",
                description=(
                    "Create or overwrite a file with UTF-8 text in the current "
                    "user's sandbox (jvspatial storage). Parent path segments are "
                    "created as needed." + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path (e.g. output/notes.md).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full file content as UTF-8 text.",
                        },
                    },
                    "required": ["path", "content"],
                },
                execute=_write_file,
            ),
            Tool(
                name="file_interface__write_binary_file",
                description=(
                    "Write binary data to a path in the user sandbox. "
                    "Pass content as base64-encoded ASCII (standard for JSON tools)."
                    + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path (e.g. output/report.pdf).",
                        },
                        "content_b64": {
                            "type": "string",
                            "description": "File bytes as a standard base64 string.",
                        },
                    },
                    "required": ["path", "content_b64"],
                },
                execute=_write_binary_file,
            ),
            Tool(
                name="file_interface__list_directory",
                description=(
                    "List immediate files and subdirectories under a relative path "
                    "in the user sandbox. Use path '' for the workspace root."
                    + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path relative to sandbox "
                                "(omit or '' for sandbox root)."
                            ),
                            "default": "",
                        },
                    },
                },
                execute=_list_directory,
            ),
            Tool(
                name="file_interface__create_directory",
                description=(
                    "Create a directory (or ensure it exists) under the user "
                    "sandbox." + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative directory path.",
                        },
                    },
                    "required": ["path"],
                },
                execute=_create_directory,
            ),
            Tool(
                name="file_interface__delete_file",
                description=(
                    "Delete a file at a path relative to the user sandbox."
                    + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path.",
                        },
                    },
                    "required": ["path"],
                },
                execute=_delete_file,
            ),
            Tool(
                name="file_interface__file_exists",
                description=(
                    "Return whether a file or marker exists at the given relative "
                    "path." + _OTHER_TOOLS
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path.",
                        },
                    },
                    "required": ["path"],
                },
                execute=_file_exists,
            ),
        ]
