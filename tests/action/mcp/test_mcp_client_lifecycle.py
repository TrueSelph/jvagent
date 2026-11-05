"""MCPClientWrapper streamable_http teardown must not cross asyncio tasks.

``streamable_http_client`` (and ``stdio_client``) wrap an ``anyio`` task group
whose cancel scope must be entered and exited in the *same* asyncio task. The
wrapper therefore runs the transport + ClientSession inside a dedicated
background task. Regression: connect in one task, disconnect from another, and
assert the transport's enter/exit happened in the same task.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from jvagent.action.mcp.client import MCPClientWrapper


class _FakeStream:
    pass


class _FakeSession:
    def __init__(self, read_stream, write_stream):
        self._read = read_stream
        self._write = write_stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return type("R", (), {"tools": []})()

    async def call_tool(self, name, arguments):
        return {"ok": True}


def _make_imports(entered):
    """Build an ``_ensure_imports`` return with a fake transport recording tasks."""

    @asynccontextmanager
    async def _transport(url, http_client=None):
        entered["enter"] = asyncio.current_task()
        try:
            yield (_FakeStream(), _FakeStream(), "sid")
        finally:
            entered["exit"] = asyncio.current_task()

    def _imports():
        return (_FakeSession, None, None, _transport)

    return _imports


async def test_streamable_http_enter_and_exit_share_one_task(monkeypatch):
    entered = {}
    monkeypatch.setattr(
        MCPClientWrapper, "_ensure_imports", staticmethod(_make_imports(entered))
    )

    wrapper = MCPClientWrapper(
        "streamable_http",
        url="http://127.0.0.1:1/api/mcp/",
        connect_timeout=2.0,
        call_timeout=2.0,
    )
    await wrapper.connect()
    assert wrapper.connected is True

    # Disconnect from a *different* task than connect — the transport must still
    # be torn down in its own task (no cross-task cancel-scope error).
    await asyncio.create_task(wrapper.disconnect())

    assert wrapper.connected is False
    assert entered["enter"] is entered["exit"]


async def test_streamable_http_connect_surfaces_transport_error(monkeypatch):
    @asynccontextmanager
    async def _boom(url, http_client=None):
        raise ValueError("boom")
        yield  # pragma: no cover

    def _imports():
        return (_FakeSession, None, None, _boom)

    monkeypatch.setattr(MCPClientWrapper, "_ensure_imports", staticmethod(_imports))

    wrapper = MCPClientWrapper(
        "streamable_http",
        url="http://127.0.0.1:1/api/mcp/",
        connect_timeout=2.0,
        call_timeout=2.0,
    )
    with pytest.raises(ValueError, match="boom"):
        await wrapper.connect()
    assert wrapper.connected is False
