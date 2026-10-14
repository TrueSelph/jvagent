"""Thin wrapper around MCP Python SDK for connect, list_tools, and call_tool.

All MCP-specific imports are confined to this module.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

from jvspatial import create_task

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

    Both transports run their context managers inside a **dedicated background
    task** that owns them (stdio and streamable_http alike). The MCP SDK's
    transports wrap ``anyio`` task groups, whose cancel scope must be exited in
    the same asyncio task that entered it; entering on an ``AsyncExitStack`` and
    closing it from another task raises ``RuntimeError: Attempted to exit cancel
    scope in a different task than it was entered in``. A dedicated task also
    avoids the stdio ExceptionGroup/BrokenResourceError race.
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
        http_client: Optional[Any] = None,
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
            http_client: Optional pre-configured httpx.AsyncClient for the
                streamable_http transport (e.g. carrying auth headers). Owned by
                this wrapper and closed on disconnect.
        """
        self._transport = transport
        self._command = command
        self._args = args or []
        self._env = env
        self._url = url
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout
        self._http_client = http_client
        self._stack: Optional[AsyncExitStack] = None
        self._session: Any = None
        self._read_stream: Any = None
        self._write_stream: Any = None
        # Background task managing the subprocess lifecycle (stdio) or the
        # streamable_http transport + ClientSession. Both are owned by a
        # dedicated task so the SDK's anyio task group is entered and exited in
        # the same asyncio task (see the class docstring).
        self._stdio_task: Optional[asyncio.Task] = None
        self._stdio_ready: Optional[asyncio.Event] = None
        self._stdio_error: Optional[Exception] = None
        self._http_task: Optional[asyncio.Task] = None
        self._http_ready: Optional[asyncio.Event] = None
        self._http_error: Optional[Exception] = None
        self._disconnect_event: Optional[asyncio.Event] = None
        self._connect_lock = asyncio.Lock()

    def _ensure_imports(self) -> Any:
        return _import_mcp()

    async def connect(self) -> None:
        """Establish MCP session (transport + ClientSession + initialize)."""
        async with self._connect_lock:
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
        self._stdio_task = await create_task(
            _run_stdio_session(), name="mcp_stdio_session", concurrent=True
        )

        # Wait for the session to be ready (or error)
        try:
            await asyncio.wait_for(
                self._stdio_ready.wait(), timeout=self._connect_timeout
            )
        except asyncio.TimeoutError:
            await self.disconnect()
            raise TimeoutError(
                f"MCP stdio connection timed out after {self._connect_timeout}s"
            )

        if self._stdio_error is not None:
            err = self._stdio_error
            await self.disconnect()
            raise err

    async def _connect_streamable_http(self) -> None:
        """Connect via streamable_http, owning the transport in a background task.

        ``streamable_http_client`` runs an ``anyio`` task group whose cancel scope
        must be exited in the same asyncio task that entered it. Entering on an
        ``AsyncExitStack`` in the caller and closing it from another task raises
        ``RuntimeError: Attempted to exit cancel scope in a different task than it
        was entered in``. Mirror the stdio path: one dedicated task owns the
        transport + ClientSession and is signalled/awaited by ``disconnect()``.
        """
        ClientSession, _, _, streamable_http_client = self._ensure_imports()

        self._http_ready = asyncio.Event()
        self._http_error = None

        async def _run_http_session() -> None:
            """Run the transport + ClientSession lifecycle as a long-lived task."""
            try:
                transport_ctx = (
                    streamable_http_client(self._url, http_client=self._http_client)
                    if self._http_client is not None
                    else streamable_http_client(self._url)
                )
                async with transport_ctx as (read_stream, write_stream, _session_id):
                    self._read_stream = read_stream
                    self._write_stream = write_stream
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        self._session = session
                        self._http_ready.set()
                        # Keep the context alive until disconnect() is called.
                        if self._disconnect_event:
                            await self._disconnect_event.wait()
            except Exception as e:  # noqa: BLE001
                self._http_error = e
                self._http_ready.set()

        self._disconnect_event = asyncio.Event()
        self._http_task = await create_task(
            _run_http_session(), name="mcp_http_session", concurrent=True
        )

        # Wait for the session to be ready (or error).
        try:
            await asyncio.wait_for(
                self._http_ready.wait(), timeout=self._connect_timeout
            )
        except asyncio.TimeoutError:
            await self.disconnect()
            raise TimeoutError(
                f"MCP streamable_http connection timed out after "
                f"{self._connect_timeout}s"
            )

        if self._http_error is not None:
            err = self._http_error
            await self.disconnect()
            raise err

    async def disconnect(self) -> None:
        """Close session and transport."""
        if self._transport == "stdio" and self._stdio_task is not None:
            # Signal the background task to exit, then cancel + await it so the
            # SDK task group is torn down in its own task.
            if self._disconnect_event:
                self._disconnect_event.set()
            self._stdio_task.cancel()
            try:
                await self._stdio_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stdio_task = None
        elif self._transport == "streamable_http" and self._http_task is not None:
            # Same dedicated-task teardown for the HTTP transport.
            if self._disconnect_event:
                self._disconnect_event.set()
            self._http_task.cancel()
            try:
                await self._http_task
            except (asyncio.CancelledError, Exception):
                pass
            self._http_task = None
        elif self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
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
