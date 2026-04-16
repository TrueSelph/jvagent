"""MCPAction: gateway action that fulfills natural language commands via an MCP server."""

import json
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.mcp.client import MCPClientWrapper
from jvagent.action.mcp.prompts import (
    TOOL_SELECTION_SYSTEM,
    build_tool_selection_prompt,
)
from jvagent.action.mcp.result import MCPFulfillResult

logger = logging.getLogger(__name__)


def _format_tools_description(tools: List[Any]) -> str:
    """Format MCP tool list for the LLM prompt."""
    parts = []
    for t in tools:
        name = getattr(t, "name", "") or ""
        desc = getattr(t, "description", None) or ""
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        schema_str = json.dumps(schema) if schema is not None else "{}"
        parts.append(f"name: {name}\ndescription: {desc}\ninputSchema: {schema_str}")
    return "\n---\n".join(parts) if parts else "(no tools available)"


def _parse_tool_selection(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response into {tool_name, arguments}. Returns None on failure."""
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
        tool_name = data.get("tool_name")
        arguments = data.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return {
            "tool_name": str(tool_name) if tool_name else "",
            "arguments": arguments,
        }
    except json.JSONDecodeError:
        return None


def _normalize_call_result(result: Any, tool_name: str) -> MCPFulfillResult:
    """Convert MCP CallToolResult to MCPFulfillResult."""
    is_error = getattr(result, "is_error", True)
    content = getattr(result, "content", None) or []
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
    structured = getattr(result, "structured_content", None)
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
    """Action that pairs with a named MCP server and exposes fulfill(natural_language_command).

    Requires a LanguageModelAction on the same agent for NL → tool + arguments mapping.
    Use get_model_action(required=True) (will raise if none configured).
    """

    server_name: str = attribute(
        default="mcp", description="Logical name for this MCP server"
    )
    transport: str = attribute(
        default="streamable_http", description="stdio or streamable_http"
    )
    command: str = attribute(default="", description="For stdio: executable to run")
    args: List[str] = attribute(
        default_factory=list, description="For stdio: command arguments"
    )
    env: Optional[Dict[str, str]] = attribute(
        default=None, description="For stdio: optional env"
    )
    url: str = attribute(default="", description="For streamable_http: endpoint URL")

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
        self._client = None
        self._tool_cache = None
        self._lock = None

    def _get_lock(self) -> Any:
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def on_register(self) -> None:
        """Set default label from server_name if label is empty."""
        await super().on_register()
        if not (getattr(self, "label", None) or "").strip():
            self.label = f"MCP ({self.server_name})"

    async def on_disable(self) -> None:
        """Disconnect MCP client when action is disabled."""
        await super().on_disable()
        await self._clear_session()

    def _get_client(self) -> MCPClientWrapper:
        if self._client is None:
            self._client = MCPClientWrapper(
                self.transport,
                command=self.command,
                args=self.args,
                env=self.env,
                url=self.url,
                connect_timeout=self.mcp_connect_timeout,
                call_timeout=self.mcp_call_timeout,
            )
        return self._client

    async def _clear_session(self) -> None:
        self._tool_cache = None
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.debug("MCP disconnect during clear: %s", e)
            self._client = None

    async def _list_tools_cached(self) -> List[Any]:
        client = self._get_client()
        if self._tool_cache is not None:
            return self._tool_cache
        try:
            tools = await client.list_tools()
            self._tool_cache = tools
            return tools
        except Exception as e:
            logger.warning("MCP list_tools failed for %s: %s", self.server_name, e)
            await self._clear_session()
            raise

    async def fulfill(self, natural_language_command: str) -> MCPFulfillResult:
        """Map NL command to one MCP tool, call it, and return a normalized result.

        Acquires the per-instance lock, ensures connection, uses LLM to select tool + args,
        validates tool name, calls the tool, and returns MCPFulfillResult.
        """
        lock = self._get_lock()
        async with lock:
            try:
                tools = await self._list_tools_cached()
            except Exception as e:
                return MCPFulfillResult(
                    text=str(e),
                    is_error=True,
                    error_kind="gateway_error",
                )
            if not tools:
                return MCPFulfillResult(
                    text="No tools available from MCP server.",
                    is_error=True,
                    error_kind="no_tool",
                )
            tools_description = _format_tools_description(tools)
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
            tool_name = (parsed.get("tool_name") or "").strip()
            arguments = parsed.get("arguments") or {}
            if not tool_name:
                return MCPFulfillResult(
                    text="No suitable tool selected for the request.",
                    is_error=True,
                    error_kind="no_tool",
                )
            names = {getattr(t, "name", "") for t in tools}
            if tool_name not in names:
                return MCPFulfillResult(
                    text=f"Invalid tool name: {tool_name}",
                    is_error=True,
                    error_kind="gateway_error",
                    tool_name=tool_name,
                )
            client = self._get_client()
            try:
                call_result = await client.call_tool(tool_name, arguments)
            except Exception as e:
                logger.warning("MCP call_tool %s failed: %s", tool_name, e)
                await self._clear_session()
                return MCPFulfillResult(
                    text=str(e),
                    is_error=True,
                    error_kind="tool_failed",
                    tool_name=tool_name,
                )
            return _normalize_call_result(call_result, tool_name)

    async def healthcheck(self) -> bool:
        """Connect if needed and list_tools with timeout; return True if healthy."""
        lock = self._get_lock()
        async with lock:
            try:
                await self._list_tools_cached()
                return True
            except Exception as e:
                logger.debug("MCP healthcheck failed for %s: %s", self.server_name, e)
                await self._clear_session()
                return False

    def get_client(self) -> MCPClientWrapper:
        """Get or create the MCP client wrapper (public accessor).

        Used by ToolExecutor to call tools directly without going through
        fulfill()'s NL-to-tool mapping.

        Returns:
            The MCPClientWrapper instance for this server.
        """
        return self._get_client()

    async def get_tools_cached(self) -> List[Any]:
        """List tools from the MCP server with caching (public accessor).

        Used by ToolExecutor to discover available tools for registration.

        Returns:
            List of MCP Tool objects (name, description, inputSchema).
        """
        return await self._list_tools_cached()
