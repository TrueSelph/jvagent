"""Thin wrapper around MCP Python SDK for connect, list_tools, and call_tool.

All MCP-specific imports are confined to this module.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _import_mcp() -> Any:
    """Import MCP SDK; keep all MCP imports inside this module."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client

    return ClientSession, StdioServerParameters, stdio_client, streamable_http_client


class MCPClientWrapper:
    """Wrapper around MCP SDK: connect (stdio or streamable_http), list_tools, call_tool.

    Session lifecycle: one long-lived session per wrapper for stdio; for streamable_http
    the same session is reused. Use connect() to establish, disconnect() to tear down.
    """

    def __init__(
        self,
        transport: str,
        *,
        command: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: str = "",
        connect_timeout: float = 10.0,
        call_timeout: float = 30.0,
    ) -> None:
        """Initialize wrapper config.

        Args:
            transport: "stdio" or "streamable_http".
            command: For stdio, executable to run.
            args: For stdio, command line arguments.
            env: For stdio, optional env dict.
            url: For streamable_http, endpoint URL.
            connect_timeout: Timeout in seconds for connect + initialize + list_tools.
            call_timeout: Timeout in seconds for call_tool.
        """
        self._transport = transport
        self._command = command
        self._args = args or []
        self._env = env
        self._url = url
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout
        self._stack: Optional[AsyncExitStack] = None
        self._session: Any = None
        self._read_stream: Any = None
        self._write_stream: Any = None

    def _ensure_imports(self) -> Any:
        return _import_mcp()

    async def connect(self) -> None:
        """Establish MCP session (transport + ClientSession + initialize)."""
        if self._session is not None:
            return
        ClientSession, StdioServerParameters, stdio_client, streamable_http_client = (
            self._ensure_imports()
        )
        stack = AsyncExitStack()
        try:
            if self._transport == "stdio":
                server_params = StdioServerParameters(
                    command=self._command,
                    args=self._args,
                    env=self._env,
                )
                transport_ctx = stdio_client(server_params)
            elif self._transport == "streamable_http":
                transport_ctx = streamable_http_client(self._url)
            else:
                raise ValueError(f"Unsupported transport: {self._transport}")

            read_stream, write_stream = await stack.enter_async_context(transport_ctx)
            self._read_stream = read_stream
            self._write_stream = write_stream
            session = ClientSession(read_stream, write_stream)
            await asyncio.wait_for(session.initialize(), timeout=self._connect_timeout)
            self._session = session
            self._stack = stack
        except Exception:
            await stack.aclose()
            raise

    async def disconnect(self) -> None:
        """Close session and transport."""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None
        self._read_stream = None
        self._write_stream = None

    @property
    def connected(self) -> bool:
        """Return True if session is active."""
        return self._session is not None

    async def list_tools(self) -> List[Any]:
        """List tools from the MCP server.

        Returns:
            List of MCP Tool objects (name, description, inputSchema).
        """
        await self.connect()
        list_result = await asyncio.wait_for(
            self._session.list_tools(),
            timeout=self._connect_timeout,
        )
        return list(getattr(list_result, "tools", []) or [])

    async def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Call an MCP tool by name with optional arguments.

        Args:
            name: Tool name.
            arguments: Optional dict of arguments.

        Returns:
            MCP CallToolResult (has .content, .is_error, .structured_content, etc.).
        """
        await self.connect()
        return await asyncio.wait_for(
            self._session.call_tool(name, arguments or {}),
            timeout=self._call_timeout,
        )
