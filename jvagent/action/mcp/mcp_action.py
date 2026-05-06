"""MCPAction: gateway action that fulfills natural language commands via MCP servers."""

import fnmatch
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.mcp.client import MCPClientWrapper
from jvagent.action.mcp.fs_server_factory import (
    build_jvfs_client,
    build_npx_filesystem_client,
    is_filesystem_mcp_server,
    strip_trailing_path_arg,
    use_jvfs_for_sandboxed_fs,
)
from jvagent.action.mcp.prompts import (
    TOOL_SELECTION_SYSTEM,
    build_tool_selection_prompt,
)
from jvagent.action.mcp.result import MCPFulfillResult
from jvagent.action.mcp.sandbox import (
    absolute_under_files_root,
    is_local_file_interface,
    provision_sandbox_dir,
    resolve_mcp_sandbox_relpath,
    resolve_sandbox_root,
)
from jvagent.core.config import parse_env_bool

logger = logging.getLogger(__name__)


def _format_mcp_exception(e: BaseException) -> str:
    """Stringify *e* and unwrap ExceptionGroup / TaskGroup so logs show the root cause.

    The MCP stdio client and asyncio TaskGroup often report failures as
    "unhandled errors in a TaskGroup (1 sub-exception)"; this expands the
    nested exception for actionable diagnostics.
    """
    subs = getattr(e, "exceptions", None)
    if isinstance(subs, tuple) and subs and type(e).__name__.endswith("ExceptionGroup"):
        if len(subs) == 1:
            inner = subs[0]
            if isinstance(inner, BaseException):
                return f"{type(e).__name__} -> {type(inner).__name__}: {inner}"
        return f"{type(e).__name__} ({len(subs)} sub-exceptions): " + " | ".join(
            f"{type(x).__name__}: {x}" for x in subs
        )
    return f"{type(e).__name__}: {e}"


def _coalesce_bool(
    server_val: Any, action_val: Optional[bool], env_key: str, default: bool = False
) -> bool:
    if server_val is not None:
        return bool(server_val)
    if action_val is not None:
        return bool(action_val)
    raw = os.getenv(env_key)
    if raw is not None and str(raw).strip():
        v = parse_env_bool(raw)
        if v is not None:
            return v
    return default


def _coalesce_sandbox_root(action_root: Optional[str], env_key: str) -> str:
    e = (os.getenv(env_key) or "").strip()
    if e:
        return e
    if action_root and str(action_root).strip():
        return str(action_root).strip()
    return ""


@dataclass
class _ServerEntry:
    """Runtime state for one configured MCP server."""

    name: str
    enabled: bool
    client: MCPClientWrapper
    lock: Any
    tools_selector: Any
    denied_tools: List[str] = field(default_factory=list)
    tool_cache: Optional[List[Any]] = None
    # Sandbox: optional per-user MCP subprocesses (user_id -> client)
    user_clients: Dict[str, MCPClientWrapper] = field(default_factory=dict)
    user_lock: Any = field(default=None)
    sandbox_mode: bool = False
    sandbox_user_scoped: bool = False
    use_jvfs: bool = False
    files_root: str = ""
    sandbox_agent_id: str = ""
    default_sandbox_user: str = "_default"
    npx_base_args: List[str] = field(default_factory=list)
    connect_timeout: float = 10.0
    call_timeout: float = 30.0
    transport: str = "stdio"
    base_command: str = ""
    mcp_npx_cmd: str = "npx"
    base_env: Optional[Dict[str, str]] = None
    base_url: str = ""


def _format_tools_description(tools: List[Tuple[str, Any]]) -> str:
    """Format MCP tool list for the LLM prompt."""
    parts = []
    for server_name, t in tools:
        name = getattr(t, "name", "") or ""
        desc = getattr(t, "description", None) or ""
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        schema_str = json.dumps(schema) if schema is not None else "{}"
        parts.append(
            f"server: {server_name}\n"
            f"name: {name}\n"
            f"description: {desc}\n"
            f"inputSchema: {schema_str}"
        )
    return "\n---\n".join(parts) if parts else "(no tools available)"


def _parse_tool_selection(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response into {server_name, tool_name, arguments}."""
    text = (response_text or "").strip()
    if not text:
        return None
    # Allow optional markdown code fence
    if "```" in text:
        start = text.find("```")
        if "json" in text[start : start + 10]:
            start = text.find("\n", start) + 1
        end = text.find("```", start)
        if end != -1:
            text = text[start:end]
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        server_name = data.get("server_name")
        tool_name = data.get("tool_name")
        arguments = data.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return {
            "server_name": str(server_name).strip() if server_name else "",
            "tool_name": str(tool_name) if tool_name else "",
            "arguments": arguments,
        }
    except json.JSONDecodeError:
        return None


def _coerce_result_field(
    result: Any, camel_name: str, snake_name: str, default: Any
) -> Any:
    """Read an MCP CallToolResult field tolerating camelCase/snake_case spellings.

    The official MCP Python SDK exposes pydantic field names in camelCase
    (e.g. ``isError``, ``structuredContent``). Some adapters, tests, or
    future SDK changes may surface snake_case aliases instead. We prefer
    whichever name is actually set on the instance ``__dict__`` (so test
    doubles like ``MagicMock`` that only set one spelling do not get
    auto-created child mocks for the other spelling), then fall back to
    plain ``getattr`` with the documented default.
    """
    inst_dict = getattr(result, "__dict__", None)
    if isinstance(inst_dict, dict):
        if camel_name in inst_dict:
            return inst_dict[camel_name]
        if snake_name in inst_dict:
            return inst_dict[snake_name]
    sentinel = object()
    val = getattr(result, camel_name, sentinel)
    if val is not sentinel:
        return val
    val = getattr(result, snake_name, sentinel)
    if val is not sentinel:
        return val
    return default


def _normalize_call_result(result: Any, tool_name: str) -> MCPFulfillResult:
    """Convert MCP CallToolResult to MCPFulfillResult."""
    is_error = bool(_coerce_result_field(result, "isError", "is_error", False))
    content = _coerce_result_field(result, "content", "content", None) or []
    raw_content: List[Any] = list(content) if isinstance(content, (list, tuple)) else []
    text_parts = []
    for item in raw_content:
        if (
            hasattr(item, "type")
            and getattr(item, "type") == "text"
            and hasattr(item, "text")
        ):
            text_parts.append(getattr(item, "text", ""))
        elif isinstance(item, dict):
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
    text = "\n".join(text_parts).strip() or ("Tool error" if is_error else "")
    structured = _coerce_result_field(
        result, "structuredContent", "structured_content", None
    )
    if structured is not None and not isinstance(structured, dict):
        structured = None
    error_kind = "tool_failed" if is_error else None
    return MCPFulfillResult(
        text=text,
        structured=structured,
        is_error=is_error,
        error_kind=error_kind,
        tool_name=tool_name,
        raw_content=raw_content if raw_content else None,
    )


class MCPAction(Action):
    """Action that pairs with configured MCP servers and exposes fulfill(nl).

    Requires a LanguageModelAction on the same agent for NL → tool + arguments mapping.
    Use get_model_action(required=True) (will raise if none configured).
    """

    servers: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "MCP server entries. Each item supports: "
            "name, enabled, transport, command, args, env, url, "
            "mcp_connect_timeout, mcp_call_timeout, tools, denied_tools, "
            "sandbox_mode, sandbox_user_scoped, sandbox_root, "
            "type (optional: jvspatial_fs | npx_filesystem for stdio filesystem servers)."
        ),
    )
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="LanguageModelAction type for NL→tool mapping",
    )
    model: str = attribute(
        default="gpt-4o-mini", description="Model for tool selection"
    )
    mcp_connect_timeout: float = attribute(
        default=10.0, description="MCP connect/init timeout (s)"
    )
    mcp_call_timeout: float = attribute(
        default=30.0, description="MCP tool call timeout (s)"
    )
    sandbox_mode: Optional[bool] = attribute(
        default=True,
        description=(
            "Default True: confine STDIO filesystem MCP to "
            "``<files_root>/<agent_id>/<user_id>/`` so files are automatically "
            "scoped per agent + user. Set False to expose the raw filesystem "
            "root (e.g. for shared content). Per-server config can override; "
            "env fallback ``MCP_FILESYSTEM_SANDBOX_MODE``."
        ),
    )
    sandbox_user_scoped: Optional[bool] = attribute(
        default=True,
        description=(
            "Default True: spawn a separate filesystem MCP subprocess per "
            "real ``user_id`` (each rooted at "
            "``<files_root>/<agent_id>/<user_id>/``) so files written by one "
            "user are not visible to another. The shared default-user "
            "subprocess is reserved for system / no-user calls. Set False "
            "to share one subprocess across users (rooted at the default "
            "user's folder). Per-server config can override; env fallback "
            "``MCP_FILESYSTEM_SANDBOX_USER_SCOPED``."
        ),
    )
    sandbox_root: Optional[str] = attribute(
        default=None,
        description="Optional override for sandbox root; defaults to jvspatial files root (JVSPATIAL_FILES_ROOT_PATH).",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._servers_by_name: Dict[str, _ServerEntry] = {}

    def _strip_trailing_path_arg(self, args: List[str]) -> List[str]:
        """Delegate to :func:`strip_trailing_path_arg` (keeps e.g. ``@scope/pkg``)."""
        return strip_trailing_path_arg(list(args))

    async def _build_server_entries(self) -> None:
        """Compile configured server entries into runtime objects."""
        import asyncio

        self._servers_by_name = {}
        seen: set[str] = set()
        ag = await self.get_agent()
        raw_agent_id = (getattr(self, "agent_id", "") or "").strip()
        if ag and not raw_agent_id:
            raw_agent_id = str(getattr(ag, "id", "") or "").strip()
        if not raw_agent_id:
            raw_agent_id = "unknown"

        def_user = (
            os.getenv("MCP_FILESYSTEM_SANDBOX_DEFAULT_USER") or "_default"
        ).strip() or "_default"

        action_sandbox = _coalesce_sandbox_root(
            self.sandbox_root, "MCP_FILESYSTEM_SANDBOX_ROOT"
        )
        files_root = resolve_sandbox_root(action_sandbox or "")

        for raw in self.servers or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                logger.warning(
                    "MCPAction: skipping server config without 'name': %s", raw
                )
                continue
            if name in seen:
                logger.warning("MCPAction: skipping duplicate server name '%s'", name)
                continue
            seen.add(name)

            transport = str(raw.get("transport") or "streamable_http")
            command = str(raw.get("command") or "")
            args = raw.get("args") or []
            if not isinstance(args, list):
                args = []
            env = raw.get("env")
            url = str(raw.get("url") or "")
            connect_timeout = float(
                raw.get("mcp_connect_timeout", self.mcp_connect_timeout)
            )
            call_timeout = float(raw.get("mcp_call_timeout", self.mcp_call_timeout))
            enabled = bool(raw.get("enabled", True))
            tools_selector = raw.get("tools", "-all")
            denied_tools_raw = raw.get("denied_tools", [])
            denied_tools = (
                [str(p) for p in denied_tools_raw]
                if isinstance(denied_tools_raw, list)
                else []
            )

            sroot = (raw.get("sandbox_root") or "").strip() or action_sandbox
            files_root = resolve_sandbox_root(sroot or "")

            # Defaults align with attribute defaults (both True): filesystem
            # MCP servers are sandboxed per agent + per user out of the box.
            # The fourth arg is the floor when both server-level and
            # action-level values are None and the env var is also unset; it
            # mirrors the attribute default for consistency.
            sb_mode = _coalesce_bool(
                raw.get("sandbox_mode"),
                self.sandbox_mode,
                "MCP_FILESYSTEM_SANDBOX_MODE",
                True,
            )
            sb_user = _coalesce_bool(
                raw.get("sandbox_user_scoped"),
                self.sandbox_user_scoped,
                "MCP_FILESYSTEM_SANDBOX_USER_SCOPED",
                True,
            )
            use_jvfs = use_jvfs_for_sandboxed_fs(raw, sb_mode)
            is_fs = transport == "stdio" and is_filesystem_mcp_server(
                {"args": args}, use_jvfs
            )

            npx_base = strip_trailing_path_arg(list(args))
            npx_cmd = (command or "npx").strip() or "npx"
            env_d = env if isinstance(env, dict) else None
            mcp_npx_cmd = npx_cmd

            client: MCPClientWrapper
            if sb_mode and is_fs and use_jvfs:
                rel = resolve_mcp_sandbox_relpath(raw_agent_id, def_user)
                cmd, a, client = build_jvfs_client(rel, connect_timeout, call_timeout)
                command, args = cmd, a
            elif sb_mode and is_fs and not use_jvfs:
                rel = resolve_mcp_sandbox_relpath(raw_agent_id, def_user)
                abs_r = absolute_under_files_root(files_root, rel)
                # Eagerly create the directory so the MCP filesystem server can
                # validate it on startup (it fails with ENOENT otherwise).
                try:
                    os.makedirs(abs_r, exist_ok=True)
                except OSError as exc:
                    logger.warning(
                        "MCPAction: could not create sandbox dir %s: %s", abs_r, exc
                    )
                command, a, client = build_npx_filesystem_client(
                    npx_cmd, npx_base, abs_r, connect_timeout, call_timeout, env_d
                )
                args = a
            else:
                client = MCPClientWrapper(
                    transport,
                    command=command,
                    args=args,
                    env=env_d,
                    url=url,
                    connect_timeout=connect_timeout,
                    call_timeout=call_timeout,
                )

            ulock = asyncio.Lock()
            self._servers_by_name[name] = _ServerEntry(
                name=name,
                enabled=enabled,
                client=client,
                lock=asyncio.Lock(),
                tools_selector=tools_selector,
                denied_tools=denied_tools,
                tool_cache=None,
                user_clients={},
                user_lock=ulock,
                sandbox_mode=sb_mode and is_fs,
                sandbox_user_scoped=sb_user and is_fs and sb_mode,
                use_jvfs=use_jvfs and is_fs and sb_mode,
                files_root=files_root,
                sandbox_agent_id=raw_agent_id,
                default_sandbox_user=def_user,
                npx_base_args=npx_base,
                connect_timeout=connect_timeout,
                call_timeout=call_timeout,
                transport=transport,
                base_command=command,
                mcp_npx_cmd=mcp_npx_cmd,
                base_env=env_d,
                base_url=url,
            )

    async def _provision_sandboxes(self) -> None:
        from jvagent.core.app import App

        app = await App.get()
        if not app or not getattr(app, "file_storage_enabled", True):
            return
        try:
            fi = await app.get_file_interface()
        except Exception as e:
            logger.warning(
                "MCPAction: could not get file interface for provision: %s", e
            )
            return
        for ent in self._servers_by_name.values():
            if not ent.sandbox_mode:
                continue
            rel = resolve_mcp_sandbox_relpath(
                ent.sandbox_agent_id, ent.default_sandbox_user
            )
            if is_local_file_interface(fi):
                abs_p = absolute_under_files_root(ent.files_root, rel)
                p = abs_p
            else:
                p = rel.replace("\\", "/")
            try:
                await provision_sandbox_dir(p, fi)
            except Exception as e:
                logger.debug("provision_sandbox for %s: %s", ent.name, e)

    async def on_register(self) -> None:
        """Build server registry and set default label if empty."""
        await super().on_register()
        await self._build_server_entries()
        await self._provision_sandboxes()
        if not (getattr(self, "label", None) or "").strip():
            server_names = self.get_server_names()
            if len(server_names) == 1:
                self.label = f"MCP ({server_names[0]})"
            else:
                self.label = "MCP"

    async def on_startup(self) -> None:
        """Rebuild in-memory server registry after app restart."""
        await super().on_startup()
        await self._build_server_entries()
        await self._provision_sandboxes()

    async def on_disable(self) -> None:
        """Disconnect MCP clients when action is disabled."""
        await super().on_disable()
        for name in self.get_server_names():
            await self._clear_session(name)

    def _get_server_entry(self, server_name: str) -> _ServerEntry:
        entry = self._servers_by_name.get(server_name)
        if not entry:
            raise ValueError(f"Unknown MCP server '{server_name}'")
        return entry

    def _filter_tools(self, entry: _ServerEntry, tools: List[Any]) -> List[Any]:
        """Apply allow/deny selectors to a server tool list."""
        selector = entry.tools_selector
        names_to_tool: Dict[str, Any] = {}
        for tool in tools:
            name = getattr(tool, "name", "") or ""
            if name:
                names_to_tool[name] = tool
        available_names = set(names_to_tool.keys())

        allowed_names: set[str]
        if isinstance(selector, str) and selector.strip() == "-all":
            allowed_names = available_names
        elif isinstance(selector, list):
            allowed_names = set()
            for pattern in selector:
                allowed_names.update(fnmatch.filter(available_names, str(pattern)))
        else:
            allowed_names = available_names

        denied_names: set[str] = set()
        for pattern in entry.denied_tools:
            denied_names.update(fnmatch.filter(allowed_names, pattern))

        selected_names = allowed_names - denied_names
        return [names_to_tool[name] for name in names_to_tool if name in selected_names]

    async def _clear_session(self, server_name: str) -> None:
        entry = self._servers_by_name.get(server_name)
        if not entry:
            return
        entry.tool_cache = None
        for _uid, ucl in list((entry.user_clients or {}).items()):
            try:
                await ucl.disconnect()
            except Exception as e:
                logger.debug("MCP user client disconnect: %s", e)
        entry.user_clients = {}
        if entry.client is not None:
            try:
                await entry.client.disconnect()
            except Exception as e:
                logger.debug("MCP disconnect during clear (%s): %s", server_name, e)

    async def _list_tools_cached(self, server_name: str) -> List[Any]:
        entry = self._get_server_entry(server_name)
        if not entry.enabled:
            return []
        if entry.tool_cache is not None:
            return self._filter_tools(entry, entry.tool_cache)

        lock = entry.lock
        async with lock:
            if entry.tool_cache is not None:
                return self._filter_tools(entry, entry.tool_cache)
            try:
                tools = await entry.client.list_tools()
                entry.tool_cache = tools
                return self._filter_tools(entry, tools)
            except Exception as e:
                detail = _format_mcp_exception(e)
                logger.warning("MCP list_tools failed for %s: %s", server_name, detail)
                await self._clear_session(server_name)
                raise RuntimeError(
                    f"MCP list_tools failed for {server_name}: {detail}"
                ) from e

    async def _resolve_tool_inventory(self) -> List[Tuple[str, Any]]:
        inventory: List[Tuple[str, Any]] = []
        for server_name in self.get_server_names():
            try:
                tools = await self._list_tools_cached(server_name)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to list tools for MCP server '{server_name}': {e}"
                ) from e
            for tool in tools:
                inventory.append((server_name, tool))
        return inventory

    async def fulfill(
        self,
        natural_language_command: str,
        user_id: Optional[str] = None,
        *,
        session_id: Optional[str] = None,
    ) -> MCPFulfillResult:
        """Map NL command to one tool across configured servers and execute it.

        Args:
            natural_language_command: The natural language request to fulfill.
            user_id: Optional authenticated caller identity. Routed to the
                per-user sandbox subprocess (``<files_root>/<agent_id>/<user_id>/``)
                when ``sandbox_user_scoped`` is enabled.
            session_id: Optional session identifier used as a fallback when
                ``user_id`` is not provided, so anonymous callers still land
                in their own per-session sandbox folder rather than the
                shared system-default folder.
        """
        from jvagent.action.mcp.sandbox import effective_user_segment

        # Resolve the segment used for sandbox routing. ``default=""`` so we
        # surface "no caller identity" to ``get_client_for_user`` as None,
        # which then falls back to the default-user subprocess.
        effective = effective_user_segment(user_id, session_id, default="")
        user_id = effective or None
        try:
            inventory = await self._resolve_tool_inventory()
        except Exception as e:
            return MCPFulfillResult(
                text=str(e),
                is_error=True,
                error_kind="gateway_error",
            )
        if not inventory:
            return MCPFulfillResult(
                text="No tools available from configured MCP servers.",
                is_error=True,
                error_kind="no_tool",
            )

        tools_description = _format_tools_description(inventory)
        user_prompt = build_tool_selection_prompt(
            natural_language_command, tools_description
        )
        model_action = await self.get_model_action(required=True)
        try:
            result = await model_action.query_sync(
                user_prompt,
                system=TOOL_SELECTION_SYSTEM,
            )
            response_text = await result.get_response()
        except Exception as e:
            logger.warning("MCP LLM tool selection failed: %s", e)
            return MCPFulfillResult(
                text=str(e),
                is_error=True,
                error_kind="gateway_error",
            )

        parsed = _parse_tool_selection(response_text)
        if not parsed:
            return MCPFulfillResult(
                text="Could not parse tool selection from model.",
                is_error=True,
                error_kind="gateway_error",
            )

        selected_server = (parsed.get("server_name") or "").strip()
        tool_name = (parsed.get("tool_name") or "").strip()
        arguments = parsed.get("arguments") or {}
        if not tool_name:
            return MCPFulfillResult(
                text="No suitable tool selected for the request.",
                is_error=True,
                error_kind="no_tool",
            )

        pairs: Dict[Tuple[str, str], Any] = {}
        by_tool_name: Dict[str, List[str]] = {}
        for server_name, tool in inventory:
            name = getattr(tool, "name", "") or ""
            if not name:
                continue
            pairs[(server_name, name)] = tool
            by_tool_name.setdefault(name, []).append(server_name)

        if not selected_server:
            servers_for_tool = by_tool_name.get(tool_name, [])
            if len(servers_for_tool) == 1:
                selected_server = servers_for_tool[0]
        if (selected_server, tool_name) not in pairs:
            return MCPFulfillResult(
                text=(
                    f"Invalid server/tool selection: "
                    f"server='{selected_server}', tool='{tool_name}'"
                ),
                is_error=True,
                error_kind="gateway_error",
                tool_name=tool_name,
            )

        try:
            client = await self.get_client_for_user(selected_server, user_id)
        except Exception:
            client = self.get_client(selected_server)
        try:
            call_result = await client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.warning(
                "MCP call_tool %s on %s failed: %s", tool_name, selected_server, e
            )
            await self._clear_session(selected_server)
            return MCPFulfillResult(
                text=str(e),
                is_error=True,
                error_kind="tool_failed",
                tool_name=tool_name,
            )
        return _normalize_call_result(call_result, tool_name)

    async def healthcheck(self, server_name: Optional[str] = None) -> bool:
        """Connect if needed and list_tools with timeout; return True if healthy."""
        if server_name:
            try:
                await self._list_tools_cached(server_name)
                return True
            except Exception as e:
                logger.debug("MCP healthcheck failed for %s: %s", server_name, e)
                await self._clear_session(server_name)
                return False

        server_names = self.get_server_names()
        if not server_names:
            return False
        for name in server_names:
            ok = await self.healthcheck(name)
            if not ok:
                return False
        return True

    def get_server_names(self) -> List[str]:
        """Return configured MCP server names."""
        return list(self._servers_by_name.keys())

    def get_client(self, server_name: str) -> MCPClientWrapper:
        """Get or create the MCP client wrapper for a configured server.

        Used by ToolExecutor to call tools directly without going through
        fulfill()'s NL-to-tool mapping.

        Returns:
            The MCPClientWrapper instance for the requested server.
        """
        return self._get_server_entry(server_name).client

    async def get_client_for_user(
        self, server_name: str, user_id: Optional[str]
    ) -> MCPClientWrapper:
        """Return the MCP client for this server, scoped to a user if configured.

        When ``sandbox_user_scoped`` is True for a filesystem stdio server, creates
        (and caches) a separate subprocess per sanitized ``user_id`` under
        ``<files_root>/<agentId>/<userId>`` (see ``resolve_mcp_sandbox_relpath``).
        The default client (``entry.client``) uses the ``MCP_FILESYSTEM_SANDBOX_DEFAULT_USER``
        path (default ``_default``) and is returned when no real user ID is available or
        when sandbox scoping is not enabled.

        Used by both ``ToolExecutor._dispatch_mcp_tool`` (LLM-driven dispatch) and
        ``fulfill()`` (NL-gateway dispatch) so that the folder always bears the caller's
        user ID rather than the startup-time default.
        """
        entry = self._get_server_entry(server_name)
        if not entry.sandbox_mode or not entry.sandbox_user_scoped:
            return entry.client
        if entry.transport != "stdio":
            return entry.client
        uid = (user_id or "").strip() or entry.default_sandbox_user or "_default"
        if uid == (entry.default_sandbox_user or "_default"):
            return entry.client
        async with entry.user_lock:
            if uid in entry.user_clients:
                return entry.user_clients[uid]
            urel = resolve_mcp_sandbox_relpath(entry.sandbox_agent_id, uid)
            from jvagent.core.app import App

            app = await App.get()
            if app and getattr(app, "file_storage_enabled", True):
                try:
                    fi = await app.get_file_interface()
                    if is_local_file_interface(fi):
                        abs_u = absolute_under_files_root(entry.files_root, urel)
                        await provision_sandbox_dir(abs_u, fi)
                    else:
                        await provision_sandbox_dir(urel.replace("\\", "/"), fi)
                except Exception as e:
                    logger.debug("user sandbox provision: %s", e)
            if entry.use_jvfs:
                _c, _a, cl = build_jvfs_client(
                    urel, entry.connect_timeout, entry.call_timeout
                )
            else:
                abs_u = absolute_under_files_root(entry.files_root, urel)
                _c, _a, cl = build_npx_filesystem_client(
                    entry.mcp_npx_cmd,
                    entry.npx_base_args,
                    abs_u,
                    entry.connect_timeout,
                    entry.call_timeout,
                    entry.base_env,
                )
            entry.user_clients[uid] = cl
            return cl

    async def get_tools_cached(self, server_name: str) -> List[Any]:
        """List tools from one MCP server with caching (public accessor).

        Used by ToolExecutor to discover available tools for registration.

        Returns:
            List of MCP Tool objects (name, description, inputSchema).
        """
        return await self._list_tools_cached(server_name)

    async def get_tools(self) -> List[Any]:
        from jvagent.tooling.tool import Tool

        action = self
        tools: List[Tool] = []

        for server_name in action.get_server_names():
            try:
                mcp_tools = await action.get_tools_cached(server_name)
            except Exception:
                continue

            for mt in mcp_tools:
                name = getattr(mt, "name", "") or ""
                desc = getattr(mt, "description", "") or ""
                schema = (
                    getattr(mt, "input_schema", None)
                    or getattr(mt, "inputSchema", None)
                    or {"type": "object", "properties": {}}
                )

                async def _dispatch(
                    sn: str = server_name,
                    tn: str = name,
                    **kwargs: Any,
                ) -> str:
                    """Forward keyword args (the model's tool args) to the MCP server.

                    Reads the immutable dispatch context set by
                    ``ToolExecutionEngine.dispatch`` so the call routes to the
                    per-user MCP subprocess (folder named after the caller's
                    ``user_id`` / ``session_id``). Falls back to the default
                    subprocess only when no context is in scope (raw scripted
                    tool execution, tests).
                    """
                    from jvagent.action.mcp.mcp_action import _normalize_call_result
                    from jvagent.action.mcp.sandbox import effective_user_segment
                    from jvagent.tooling.tool_executor import get_dispatch_context

                    ctx = get_dispatch_context()
                    if ctx is not None:
                        uid = (
                            effective_user_segment(
                                ctx.user_id,
                                ctx.session_id,
                                default="",
                            )
                            or None
                        )
                        try:
                            client = await action.get_client_for_user(sn, uid)
                        except Exception as e:
                            logger.warning(
                                "MCP get_client_for_user failed (server=%s, "
                                "user=%s); falling back to default client: %s",
                                sn,
                                uid,
                                e,
                            )
                            client = action.get_client(sn)
                    else:
                        client = action.get_client(sn)

                    result = await client.call_tool(tn, dict(kwargs))
                    n = _normalize_call_result(result, tn)
                    if n.is_error and n.text:
                        return f"Error: {n.text}"
                    return n.text

                tools.append(
                    Tool(
                        name=f"mcp_{server_name}__{name}",
                        description=desc,
                        parameters_schema=(
                            schema
                            if isinstance(schema, dict)
                            else {"type": "object", "properties": {}}
                        ),
                        execute=_dispatch,
                    )
                )

        return tools
