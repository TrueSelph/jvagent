"""ToolExecutor: bridges ModelActionResult.tool_calls to actual execution.

Reuses existing ToolManager, ToolDefinition, ToolCall, and MCPClientWrapper.
Bypasses MCPAction.fulfill() (which does its own NL-to-tool mapping) and calls
MCPClientWrapper.call_tool() directly for deterministic LLM-driven dispatch.
"""

import asyncio
import fnmatch
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from jvagent.action.model.language.tools import ToolCall, ToolDefinition, ToolManager

logger = logging.getLogger(__name__)


class ToolDispatchError(Exception):
    """Raised when a tool dispatch fails."""


class ToolExecutor:
    """Dispatches tool calls from LLM responses to actual executors.

    ToolExecutor is NOT an Action -- it is a runtime helper instantiated by
    ThinkingInteractAction during execute(). It aggregates available tools
    from MCP servers and local handlers, validates calls, and dispatches
    them with timeout and error sanitization.

    Args:
        call_timeout: Timeout in seconds for each individual tool call.
        validate_calls: If True, validate tool calls before dispatch.
        max_concurrent_calls: Maximum concurrent tool executions.
        sanitize_errors: If True, replace internal error details with
            generic messages before returning to the LLM.
    """

    def __init__(
        self,
        call_timeout: float = 60.0,
        validate_calls: bool = True,
        max_concurrent_calls: int = 5,
        sanitize_errors: bool = True,
    ) -> None:
        self._tool_manager = ToolManager()
        self._handlers: Dict[str, Tuple[str, Any]] = {}  # name -> (kind, handler)
        self.call_timeout = call_timeout
        self.validate_calls = validate_calls
        self.max_concurrent_calls = max_concurrent_calls
        self.sanitize_errors = sanitize_errors

    async def initialize(
        self,
        visitor: Any,
        tool_servers: Optional[List[str]] = None,
        skill: Any = None,
        allowed_tool_patterns: Optional[List[str]] = None,
        denied_tool_patterns: Optional[List[str]] = None,
    ) -> None:
        """Discover and register all available tools.

        1. Find MCPAction instances by server_name from the agent graph
        2. Call list_tools() on each MCP client -> register as ToolDefinition
        3. Register any local Python tool handlers
        4. If skill provided, filter via skill.get_tool_filter()
        5. Apply allowed/denied tool pattern filters
        6. Build ToolManager with all registered tools

        Args:
            visitor: The InteractWalker (provides agent access).
            tool_servers: Names of MCPAction instances to use.
            skill: Optional SkillAction for tool filtering.
            allowed_tool_patterns: Glob patterns for tool names to allow.
            denied_tool_patterns: Glob patterns for tool names to deny.
        """
        tool_servers = tool_servers or []

        # Register MCP tools
        for server_name in tool_servers:
            try:
                await self._register_mcp_server(visitor, server_name)
            except Exception as e:
                logger.warning(
                    "ToolExecutor: failed to register MCP server '%s': %s",
                    server_name,
                    e,
                    exc_info=True,
                )

        # Apply skill tool filter
        if skill is not None:
            available_tools = list(self._tool_manager.tools.values())
            filtered = skill.get_tool_filter(available_tools)
            # Rebuild tool manager with only filtered tools
            self._tool_manager.tools = {t.name: t for t in filtered}
            # Also filter handlers
            filtered_names = {t.name for t in filtered}
            self._handlers = {
                k: v for k, v in self._handlers.items() if k in filtered_names
            }

        # Apply pattern filters
        if allowed_tool_patterns or denied_tool_patterns:
            self._apply_pattern_filters(allowed_tool_patterns, denied_tool_patterns)

        logger.info(
            "ToolExecutor initialized with %d tools: %s",
            len(self._tool_manager.tools),
            list(self._tool_manager.tools.keys()),
        )

    async def _register_mcp_server(self, visitor: Any, server_name: str) -> None:
        """Register all tools from an MCP server.

        Args:
            visitor: The InteractWalker.
            server_name: Logical name of the MCPAction instance.
        """
        from jvagent.action.mcp.mcp_action import MCPAction

        agent = getattr(visitor, "_agent", None)
        if agent is None:
            logger.warning(
                "ToolExecutor: no agent on visitor, cannot find MCP server '%s'",
                server_name,
            )
            return

        # Find the MCPAction by server_name — iterate all agent actions
        # (Agent doesn't have get_action() with filter support)
        mcp_action = None
        try:
            all_actions = await agent.get_actions()
            for action in all_actions:
                if (
                    isinstance(action, MCPAction)
                    and getattr(action, "server_name", None) == server_name
                ):
                    mcp_action = action
                    break
        except Exception as e:
            logger.warning("ToolExecutor: error getting actions from agent: %s", e)
            return

        if not mcp_action:
            logger.warning(
                "ToolExecutor: MCPAction with server_name '%s' not found", server_name
            )
            return

        if not getattr(mcp_action, "enabled", True):
            logger.debug(
                "ToolExecutor: MCPAction '%s' is disabled, skipping", server_name
            )
            return

        # Access the client and tools directly, bypassing fulfill()
        client = mcp_action.get_client()
        tools = await mcp_action.get_tools_cached()

        for mcp_tool in tools:
            name = getattr(mcp_tool, "name", "") or ""
            description = getattr(mcp_tool, "description", "") or ""
            input_schema = (
                getattr(mcp_tool, "input_schema", None)
                or getattr(mcp_tool, "inputSchema", None)
                or {"type": "object", "properties": {}}
            )
            try:
                self._tool_manager.register_tool(
                    name=name,
                    description=description,
                    parameters=(
                        input_schema
                        if isinstance(input_schema, dict)
                        else {"type": "object", "properties": {}}
                    ),
                )
                self._handlers[name] = ("mcp", mcp_action)
            except ValueError as e:
                logger.warning("ToolExecutor: skipping MCP tool '%s': %s", name, e)

    def _apply_pattern_filters(
        self,
        allowed_patterns: Optional[List[str]] = None,
        denied_patterns: Optional[List[str]] = None,
    ) -> None:
        """Filter registered tools by allowed/denied glob patterns."""
        tool_names = set(self._tool_manager.tools.keys())

        if allowed_patterns:
            allowed = set()
            for pattern in allowed_patterns:
                allowed.update(fnmatch.filter(tool_names, pattern))
            tool_names = tool_names & allowed

        if denied_patterns:
            denied = set()
            for pattern in denied_patterns:
                denied.update(fnmatch.filter(tool_names, pattern))
            tool_names = tool_names - denied

        # Remove tools not in filtered set
        removed = set(self._tool_manager.tools.keys()) - tool_names
        for name in removed:
            del self._tool_manager.tools[name]
            self._handlers.pop(name, None)

    def register_local_tool(
        self,
        name: str,
        handler: Callable,
        description: str,
        parameters: Dict[str, Any],
    ) -> ToolDefinition:
        """Register a local Python function as a tool.

        Args:
            name: Tool name (alphanumeric + underscores).
            handler: Async callable implementing the tool.
            description: Human-readable description.
            parameters: JSON Schema for parameters.

        Returns:
            The registered ToolDefinition.
        """
        tool_def = self._tool_manager.register_tool(name, description, parameters)
        self._handlers[name] = ("local", handler)
        return tool_def

    async def dispatch(
        self, tool_calls: List[Dict[str, Any]], visitor: Any = None
    ) -> List[Dict[str, Any]]:
        """Dispatch a batch of tool calls and return tool result messages.

        Each tool call is validated, executed with a timeout, and the result
        is formatted as a tool result message ready to append to the
        conversation for the next LLM iteration.

        Args:
            tool_calls: List of raw tool call dicts from ModelActionResult.
            visitor: Optional InteractWalker for context.

        Returns:
            List of tool result messages in the format:
            [{"role": "tool", "tool_call_id": str, "content": str}]
        """
        parsed_calls = self._tool_manager.parse_tool_calls(tool_calls)

        # Execute concurrently with limit
        semaphore = asyncio.Semaphore(self.max_concurrent_calls)

        async def _dispatch_one(call: ToolCall) -> Dict[str, Any]:
            async with semaphore:
                return await self._dispatch_single(call)

        results = await asyncio.gather(
            *[_dispatch_one(call) for call in parsed_calls],
            return_exceptions=False,
        )
        return list(results)

    async def _dispatch_single(self, call: ToolCall) -> Dict[str, Any]:
        """Dispatch a single tool call with validation and timeout.

        Args:
            call: The ToolCall to dispatch.

        Returns:
            Tool result message dict.
        """
        # Validate
        if self.validate_calls:
            is_valid, error = self._tool_manager.validate_tool_call(call)
            if not is_valid:
                return self._make_error_result(call.id, error or "Validation failed")

        # Find handler
        handler_entry = self._handlers.get(call.name)
        if handler_entry is None:
            available = list(self._tool_manager.tools.keys())
            return self._make_error_result(
                call.id,
                f"Tool '{call.name}' is not available. Available tools: {available}",
            )

        kind, handler = handler_entry

        try:
            start = time.monotonic()
            if kind == "mcp":
                result_text = await asyncio.wait_for(
                    self._dispatch_mcp_tool(call, handler),
                    timeout=self.call_timeout,
                )
            elif kind == "local":
                result_text = await asyncio.wait_for(
                    self._dispatch_local_tool(call, handler),
                    timeout=self.call_timeout,
                )
            else:
                return self._make_error_result(call.id, f"Unknown handler kind: {kind}")

            duration_ms = int((time.monotonic() - start) * 1000)
            logger.debug("ToolExecutor: %s completed in %dms", call.name, duration_ms)
            return {
                "role": "tool",
                "tool_call_id": call.id,
                "content": result_text,
            }

        except asyncio.TimeoutError:
            return self._make_error_result(
                call.id,
                f"Tool call timed out after {self.call_timeout}s",
            )
        except Exception as e:
            logger.warning("ToolExecutor: %s failed: %s", call.name, e)
            if self.sanitize_errors:
                return self._make_error_result(
                    call.id, f"Tool execution failed: {call.name}"
                )
            return self._make_error_result(call.id, str(e))

    async def _dispatch_mcp_tool(self, call: ToolCall, mcp_action: Any) -> str:
        """Execute a tool call against an MCP server.

        Calls MCPClientWrapper.call_tool() directly, bypassing fulfill().

        Args:
            call: The ToolCall.
            mcp_action: The MCPAction instance.

        Returns:
            Tool result as string.
        """
        client = mcp_action.get_client()
        call_result = await client.call_tool(call.name, call.arguments)

        # Normalize result (same logic as mcp_action._normalize_call_result)
        is_error = getattr(call_result, "is_error", False)
        content = getattr(call_result, "content", None) or []
        raw_content = list(content) if isinstance(content, (list, tuple)) else []
        text_parts = []
        for item in raw_content:
            if (
                hasattr(item, "type")
                and getattr(item, "type") == "text"
                and hasattr(item, "text")
            ):
                text_parts.append(getattr(item, "text", ""))
            elif isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))

        text = "\n".join(text_parts).strip() or ("Tool error" if is_error else "")
        if is_error and text:
            raise ToolDispatchError(text)
        return text

    async def _dispatch_local_tool(self, call: ToolCall, handler: Callable) -> str:
        """Execute a local Python tool call.

        Args:
            call: The ToolCall.
            handler: The async callable.

        Returns:
            Tool result as string.
        """
        result = await handler(call.arguments)
        if isinstance(result, str):
            return result
        import json

        return json.dumps(result) if not isinstance(result, str) else result

    def _make_error_result(self, tool_call_id: str, message: str) -> Dict[str, Any]:
        """Create a tool error result message.

        Args:
            tool_call_id: The tool call ID.
            message: Error message.

        Returns:
            Tool result message dict with error content.
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"Error: {message}",
        }

    def get_tools_list(self) -> List[Dict[str, Any]]:
        """Return all registered tools in OpenAI function-calling format.

        Returns:
            List of tool definition dicts suitable for LLM query.
        """
        return self._tool_manager.get_tools_list()

    def get_tool_names(self) -> Set[str]:
        """Return the set of registered tool names."""
        return set(self._tool_manager.tools.keys())

    async def cleanup(self) -> None:
        """Clean up resources after the loop completes.

        Currently a no-op since MCP sessions are owned by MCPAction.
        """
        pass
