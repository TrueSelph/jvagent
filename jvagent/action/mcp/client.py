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

    Session lifecycle: one long-lived session per wrapper. Use connect() to
    establish, disconnect() to tear down.

    For stdio transport, the subprocess and task group lifecycle are managed by
    running the stdio_client context manager as a background task. This avoids
    the ExceptionGroup/BrokenResourceError that occurs when using AsyncExitStack
    to manage the stdio_client's task group.
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
        # For stdio: background task managing the subprocess lifecycle
        self._stdio_task: Optional[asyncio.Task] = None
        self._stdio_ready: Optional[asyncio.Event] = None
        self._stdio_error: Optional[Exception] = None

    def _ensure_imports(self) -> Any:
        return _import_mcp()

    async def connect(self) -> None:
        """Establish MCP session (transport + ClientSession + initialize)."""
        if self._session is not None:
            return

        if self._transport == "stdio":
            await self._connect_stdio()
        elif self._transport == "streamable_http":
            await self._connect_streamable_http()
        else:
            raise ValueError(f"Unsupported transport: {self._transport}")

    async def _connect_stdio(self) -> None:
        """Connect via stdio transport using a background task.

        The stdio_client context manager manages a subprocess and a task group
        with reader/writer coroutines. Using AsyncExitStack with this context
        manager causes ExceptionGroup/BrokenResourceError because the task group
        cleanup races with the exit stack. Instead, we run the entire
        stdio_client + ClientSession lifecycle in a background task, passing
        the session back via an event.
        """
        ClientSession, StdioServerParameters, stdio_client, _ = self._ensure_imports()

        self._stdio_ready = asyncio.Event()
        self._stdio_error = None

        async def _run_stdio_session():
            """Run the stdio_client + ClientSession lifecycle as a long-lived task."""
            server_params = StdioServerParameters(
                command=self._command,
                args=self._args,
                env=self._env,
            )
            try:
                async with stdio_client(server_params) as (read_stream, write_stream):
                    self._read_stream = read_stream
                    self._write_stream = write_stream
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        self._session = session
                        self._stdio_ready.set()
                        # Keep the context alive until disconnect() is called
                        # Wait on a signal that disconnect was requested
                        if self._disconnect_event:
                            await self._disconnect_event.wait()
            except Exception as e:
                self._stdio_error = e
                self._stdio_ready.set()

        self._disconnect_event = asyncio.Event()
        self._stdio_task = asyncio.create_task(_run_stdio_session())

        # Wait for the session to be ready (or error)
        try:
            await asyncio.wait_for(
                self._stdio_ready.wait(), timeout=self._connect_timeout
            )
        except asyncio.TimeoutError:
            self._stdio_task.cancel()
            raise TimeoutError(
                f"MCP stdio connection timed out after {self._connect_timeout}s"
            )

        if self._stdio_error is not None:
            raise self._stdio_error

    async def _connect_streamable_http(self) -> None:
        """Connect via streamable_http transport using AsyncExitStack."""
        _, _, _, streamable_http_client = self._ensure_imports()
        ClientSession = self._ensure_imports()[0]

        stack = AsyncExitStack()
        try:
            transport_ctx = streamable_http_client(self._url)
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
        if self._transport == "stdio" and self._stdio_task is not None:
            # Signal the background task to exit
            if self._disconnect_event:
                self._disconnect_event.set()
            self._stdio_task.cancel()
            try:
                await self._stdio_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stdio_task = None
        elif self._stack is not None:
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
            MCP CallToolResult (has .content, .isError, .structuredContent, etc.).
        """
        await self.connect()
        return await asyncio.wait_for(
            self._session.call_tool(name, arguments or {}),
            timeout=self._call_timeout,
        )
