"""ToolExecutor: bridges ModelActionResult.tool_calls to actual execution.

Reuses existing ToolManager, ToolDefinition, ToolCall, and MCPClientWrapper.
Bypasses MCPAction.fulfill() (which does its own NL-to-tool mapping) and calls
MCPClientWrapper.call_tool() directly for deterministic LLM-driven dispatch.
"""

import asyncio
import fnmatch
import importlib.util
import logging
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from jvagent.action.mcp.mcp_action import _normalize_call_result
from jvagent.action.model.language.tools import ToolCall, ToolDefinition, ToolManager
from jvagent.action.skill.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolDispatchError(Exception):
    """Raised when a tool dispatch fails."""


class ToolExecutor:
    """Dispatches tool calls from LLM responses to actual executors.

    ToolExecutor is NOT an Action -- it is a runtime helper instantiated by
    SkillInteractAction during execute(). It aggregates available tools
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
        allowed_tool_paths: Optional[List[str]] = None,
    ) -> None:
        self._tool_manager = ToolManager()
        self._handlers: Dict[str, Tuple[str, Any]] = {}  # name -> (kind, handler)
        self._registry = ToolRegistry()
        self._skill_bundles: Dict[str, Dict[str, Any]] = {}
        self._active_skill_bundles: Set[str] = set()
        self.call_timeout = call_timeout
        self.max_concurrent_calls = max_concurrent_calls
        self.validate_calls = validate_calls
        self.sanitize_errors = sanitize_errors
        self._allowed_tool_paths: List[str] = allowed_tool_paths or []

    @property
    def activated_skills(self) -> Set[str]:
        """Set of skill names that have been activated via read_skill."""
        return self._active_skill_bundles

    async def initialize(
        self,
        visitor: Any,
        tool_servers: Optional[List[str]] = None,
        allowed_tool_patterns: Optional[List[str]] = None,
        denied_tool_patterns: Optional[List[str]] = None,
        local_tools_paths: Optional[List[str]] = None,
    ) -> None:
        """Discover and register all available tools.

        1. Find MCPAction instances by server_name from the agent graph
        2. Call list_tools() on each MCP client -> register as ToolDefinition
        3. Register any local Python tool handlers
        4. If local_tools_path provided, scan directory for tools
        5. Apply allowed/denied tool pattern filters
        6. Build ToolManager with all registered tools

        Args:
            visitor: The InteractWalker (provides agent access).
            tool_servers: Names of MCPAction instances to use.
            allowed_tool_patterns: Glob patterns for tool name to allow.
            denied_tool_patterns: Glob patterns for tool name to deny.
            local_tools_paths: Optional list of folders to scan for .py tools.
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

        # Register tools from local directories
        if local_tools_paths:
            for path in local_tools_paths:
                try:
                    self._discover_local_tools(path)
                except Exception as e:
                    logger.error(
                        "ToolExecutor: failed to discover tools in '%s': %s",
                        path,
                        e,
                        exc_info=True,
                    )

        # Apply pattern filters
        if allowed_tool_patterns or denied_tool_patterns:
            self._apply_pattern_filters(allowed_tool_patterns, denied_tool_patterns)

        logger.info(
            "ToolExecutor initialized with %d tools: %s",
            len(self._tool_manager.tools),
            list(self._tool_manager.tools.keys()),
        )

    def _register_tool_with_registry(
        self,
        *,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        source: str,
        handler_kind: str,
        handler: Any,
        fq_name: Optional[str] = None,
        prefix: Optional[str] = None,
    ) -> str:
        """Register a tool across registry, ToolManager, and dispatch handlers."""
        handle = self._registry.register(
            name=name,
            source=source,
            schema=parameters if isinstance(parameters, dict) else {},
            dispatch=handler,
            fq_name=fq_name,
            prefix=prefix,
        )
        self._tool_manager.register_tool(
            name=handle.name,
            description=description,
            parameters=parameters,
        )
        self._handlers[handle.name] = (handler_kind, handler)
        return handle.name

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

        # Find an MCPAction hosting this server_name — iterate all agent actions
        # (Agent doesn't have get_action() with filter support)
        mcp_action = None
        try:
            all_actions = await agent.get_actions()
            for action in all_actions:
                if not isinstance(action, MCPAction):
                    continue
                if server_name in action.get_server_names():
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
        tools = await mcp_action.get_tools_cached(server_name)

        for mcp_tool in tools:
            name = getattr(mcp_tool, "name", "") or ""
            description = getattr(mcp_tool, "description", "") or ""
            input_schema = (
                getattr(mcp_tool, "input_schema", None)
                or getattr(mcp_tool, "inputSchema", None)
                or {"type": "object", "properties": {}}
            )
            try:
                registered_name = self._register_tool_with_registry(
                    name=name,
                    description=description,
                    parameters=(
                        input_schema
                        if isinstance(input_schema, dict)
                        else {"type": "object", "properties": {}}
                    ),
                    source="mcp",
                    handler_kind="mcp",
                    handler=(mcp_action, server_name),
                    fq_name=f"mcp:{server_name}:{name}",
                    prefix=f"mcp_{server_name}",
                )
                logger.debug(
                    "ToolExecutor: registered MCP tool '%s' as '%s'",
                    name,
                    registered_name,
                )
            except ValueError as e:
                logger.warning("ToolExecutor: skipping MCP tool '%s': %s", name, e)

    def _discover_local_tools(self, path: str) -> None:
        """Scan a directory for .py files and register them as tools.

        Expected format in each .py file:
        - get_tool_definition() -> dict: Returns OpenAI-format tool definition.
        - execute(arguments: dict) -> Any: Async function implementing the tool.

        Args:
            path: Absolute path to the tools directory.
        """
        if not os.path.isdir(path):
            logger.warning(
                "ToolExecutor: local_tools_path '%s' is not a directory", path
            )
            return

        logger.info("ToolExecutor: scanning for local tools in '%s'", path)

        for filename in os.listdir(path):
            if filename.startswith("_") or not filename.endswith(".py"):
                continue

            name = filename[:-3]  # Remove .py
            file_path = os.path.join(path, filename)

            try:
                # Dynamic import
                spec = importlib.util.spec_from_file_location(name, file_path)
                if not spec or not spec.loader:
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)

                # Check for required functions
                get_def = getattr(module, "get_tool_definition", None)
                handler = getattr(module, "execute", None)

                if not get_def or not handler:
                    logger.debug(
                        "ToolExecutor: file '%s' missing get_tool_definition or execute",
                        filename,
                    )
                    continue

                tool_def_dict = get_def()
                if not isinstance(tool_def_dict, dict):
                    logger.warning(
                        "ToolExecutor: %s.get_tool_definition() did not return dict",
                        name,
                    )
                    continue
                tool_name = tool_def_dict.get("function", {}).get("name")
                if not tool_name:
                    tool_name = tool_def_dict.get("name")

                if not tool_name:
                    logger.warning("ToolExecutor: tool in %s missing name", filename)
                    continue

                self.register_dynamic_tool(tool_name, tool_def_dict, handler)

            except Exception as e:
                logger.error(
                    "ToolExecutor: failed to load tool from '%s': %s",
                    filename,
                    e,
                    exc_info=True,
                )

    def register_dynamic_tool(
        self, name: str, tool_def_dict: Dict[str, Any], handler: Callable
    ) -> None:
        """Register a dynamic functional tool (like a locally discovered or runtime-generated tool)."""
        description = tool_def_dict.get("function", {}).get(
            "description"
        ) or tool_def_dict.get("description", "")
        parameters = tool_def_dict.get("function", {}).get(
            "parameters"
        ) or tool_def_dict.get("parameters", {"type": "object", "properties": {}})

        try:
            registered_name = self._register_tool_with_registry(
                name=name,
                description=description,
                parameters=parameters,
                source="dynamic",
                handler_kind="local",
                handler=handler,
                fq_name=f"dynamic:{name}",
                prefix="dynamic",
            )
            logger.debug(
                "ToolExecutor: registered dynamic tool '%s' as '%s'",
                name,
                registered_name,
            )
        except ValueError as e:
            logger.warning("ToolExecutor: skipping dynamic tool '%s': %s", name, e)

    def register_skill_bundle(
        self,
        skill_name: str,
        dir_path: str,
        tool_files: Optional[List[str]] = None,
        allowed_tools: Optional[List[str]] = None,
    ) -> None:
        """Register metadata for a skill bundle without exposing its tools yet."""
        self._skill_bundles[skill_name] = {
            "dir_path": dir_path,
            "tool_files": list(tool_files or []),
            "allowed_tools": set(allowed_tools or []),
        }

    async def activate_skill(self, skill_name: str) -> List[str]:
        """Load and register tool modules for a skill bundle on demand."""
        bundle = self._skill_bundles.get(skill_name)
        if not bundle:
            raise ToolDispatchError(f"Skill bundle '{skill_name}' is not registered")

        if skill_name in self._active_skill_bundles:
            return []

        registered: List[str] = []
        allowed_tools: Set[str] = bundle.get("allowed_tools", set())
        for file_path in bundle.get("tool_files", []):
            loaded_tool = self._load_dynamic_tool_from_file(
                file_path=file_path,
                module_prefix=f"jvagent_skill_{skill_name}",
                allowed_tools=allowed_tools if allowed_tools else None,
                tool_name_prefix=skill_name,
            )
            if loaded_tool:
                registered.append(loaded_tool)

        self._active_skill_bundles.add(skill_name)
        return registered

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
            self._registry.remove(name)

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
        registered_name = self._register_tool_with_registry(
            name=name,
            description=description,
            parameters=parameters,
            source="local",
            handler_kind="local",
            handler=handler,
            fq_name=f"local:{name}",
            prefix="local",
        )
        tool_def = self._tool_manager.get_tool(registered_name)
        if tool_def is None:
            raise ValueError(f"Failed to register local tool '{name}'")
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
                return await self._dispatch_single(call, visitor=visitor)

        results = await asyncio.gather(
            *[_dispatch_one(call) for call in parsed_calls],
            return_exceptions=False,
        )
        return list(results)

    async def _dispatch_single(
        self, call: ToolCall, visitor: Any = None
    ) -> Dict[str, Any]:
        """Dispatch a single tool call with validation and timeout.

        Args:
            call: The ToolCall to dispatch.
            visitor: Optional InteractWalker for context.

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
                    self._dispatch_local_tool(call, handler, visitor=visitor),
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

    async def _dispatch_mcp_tool(self, call: ToolCall, mcp_handler: Any) -> str:
        """Execute a tool call against an MCP server.

        Calls MCPClientWrapper.call_tool() directly, bypassing fulfill().

        Args:
            call: The ToolCall.
            mcp_handler: Tuple of (MCPAction instance, server_name).

        Returns:
            Tool result as string.
        """
        mcp_action, server_name = mcp_handler
        client = mcp_action.get_client(server_name)
        raw_tool_name = call.name
        handle = self._registry.get(call.name)
        if handle and handle.source == "mcp":
            fq = handle.fq_name.split(":")
            if len(fq) >= 3:
                raw_tool_name = fq[-1]

        call_result = await client.call_tool(raw_tool_name, call.arguments)
        normalized = _normalize_call_result(call_result, raw_tool_name)
        if normalized.is_error and normalized.text:
            raise ToolDispatchError(normalized.text)
        return normalized.text

    async def _dispatch_local_tool(
        self, call: ToolCall, handler: Callable, visitor: Any = None
    ) -> str:
        """Execute a local Python tool call.

        Args:
            call: The ToolCall.
            handler: The async callable.
            visitor: Optional InteractWalker for context.

        Returns:
            Tool result as string.
        """
        import inspect

        # Check if handler accepts visitor
        sig = inspect.signature(handler)
        maybe_result: Any
        if "visitor" in sig.parameters:
            maybe_result = handler(call.arguments, visitor=visitor)
        else:
            maybe_result = handler(call.arguments)

        result = (
            await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
        )

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
        return set(self._registry.names())

    async def cleanup(self) -> None:
        """Clean up resources after the loop completes."""
        self._handlers.clear()
        self._skill_bundles.clear()
        self._active_skill_bundles.clear()
        for name in list(self._registry.names()):
            self._registry.remove(name)

    def _load_dynamic_tool_from_file(
        self,
        file_path: str,
        module_prefix: str,
        allowed_tools: Optional[Set[str]] = None,
        tool_name_prefix: Optional[str] = None,
    ) -> Optional[str]:
        """Import one local tool module and register it if valid."""
        from pathlib import Path

        source = Path(file_path).resolve()
        if not source.is_file():
            return None
        if source.name.startswith("_") or source.suffix != ".py":
            return None

        # Path validation: reject traversal and enforce allowed directories
        try:
            source.relative_to(Path.cwd().resolve())
        except ValueError:
            if self._allowed_tool_paths:
                allowed = any(
                    str(source).startswith(str(Path(p).resolve()))
                    for p in self._allowed_tool_paths
                )
                if not allowed:
                    logger.warning(
                        "ToolExecutor: rejected file outside allowed paths: %s",
                        source,
                    )
                    return None
            # If no allowed_tool_paths configured, allow cwd-relative only

        # Deterministic, safe module name
        safe_stem = re.sub(r"[^a-zA-Z0-9_]", "_", source.stem)
        module_name = f"{module_prefix}_{safe_stem}"

        spec = importlib.util.spec_from_file_location(module_name, str(source))
        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        get_def = getattr(module, "get_tool_definition", None)
        handler = getattr(module, "execute", None)
        if not get_def or not handler:
            return None

        tool_def_dict = get_def()
        if not isinstance(tool_def_dict, dict):
            return None

        tool_name = tool_def_dict.get("function", {}).get("name") or tool_def_dict.get(
            "name"
        )
        if not tool_name:
            return None

        # Check allowed_tools: match both bare and namespaced names
        if allowed_tools is not None:
            namespaced_name = (
                f"{tool_name_prefix}__{tool_name}" if tool_name_prefix else None
            )
            if tool_name not in allowed_tools and (
                namespaced_name is None or namespaced_name not in allowed_tools
            ):
                return None

        # Namespace tool name with skill prefix to avoid collisions
        # e.g. "search" from pageindex_search → "pageindex_search__search"
        registered_name = tool_name
        if tool_name_prefix:
            registered_name = f"{tool_name_prefix}__{tool_name}"
            # Update the tool definition so the LLM sees the namespaced name
            if "function" in tool_def_dict:
                tool_def_dict["function"]["name"] = registered_name
            else:
                tool_def_dict["name"] = registered_name

        self.register_dynamic_tool(registered_name, tool_def_dict, handler)
        return registered_name
