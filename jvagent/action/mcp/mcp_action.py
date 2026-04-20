"""MCPAction: gateway action that fulfills natural language commands via MCP servers."""

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.mcp.client import MCPClientWrapper
from jvagent.action.mcp.prompts import (
    TOOL_SELECTION_SYSTEM,
    build_tool_selection_prompt,
)
from jvagent.action.mcp.result import MCPFulfillResult

logger = logging.getLogger(__name__)


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
            "mcp_connect_timeout, mcp_call_timeout, tools, denied_tools."
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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._servers_by_name: Dict[str, _ServerEntry] = {}

    def _build_server_entries(self) -> None:
        """Compile configured server entries into runtime objects."""
        import asyncio

        self._servers_by_name = {}
        seen: set[str] = set()
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
            env = raw.get("env")
            url = str(raw.get("url") or "")
            connect_timeout = float(raw.get("mcp_connect_timeout", 10.0))
            call_timeout = float(raw.get("mcp_call_timeout", 30.0))
            enabled = bool(raw.get("enabled", True))
            tools_selector = raw.get("tools", "-all")
            denied_tools_raw = raw.get("denied_tools", [])
            denied_tools = (
                [str(p) for p in denied_tools_raw]
                if isinstance(denied_tools_raw, list)
                else []
            )

            client = MCPClientWrapper(
                transport,
                command=command,
                args=args if isinstance(args, list) else [],
                env=env if isinstance(env, dict) else None,
                url=url,
                connect_timeout=connect_timeout,
                call_timeout=call_timeout,
            )
            self._servers_by_name[name] = _ServerEntry(
                name=name,
                enabled=enabled,
                client=client,
                lock=asyncio.Lock(),
                tools_selector=tools_selector,
                denied_tools=denied_tools,
            )

    async def on_register(self) -> None:
        """Build server registry and set default label if empty."""
        await super().on_register()
        self._build_server_entries()
        if not (getattr(self, "label", None) or "").strip():
            server_names = self.get_server_names()
            if len(server_names) == 1:
                self.label = f"MCP ({server_names[0]})"
            else:
                self.label = "MCP"

    async def on_startup(self) -> None:
        """Rebuild in-memory server registry after app restart."""
        await super().on_startup()
        self._build_server_entries()

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
                logger.warning("MCP list_tools failed for %s: %s", server_name, e)
                await self._clear_session(server_name)
                raise

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

    async def fulfill(self, natural_language_command: str) -> MCPFulfillResult:
        """Map NL command to one tool across configured servers and execute it."""
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

    async def get_tools_cached(self, server_name: str) -> List[Any]:
        """List tools from one MCP server with caching (public accessor).

        Used by ToolExecutor to discover available tools for registration.

        Returns:
            List of MCP Tool objects (name, description, inputSchema).
        """
        return await self._list_tools_cached(server_name)
