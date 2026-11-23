"""MCP targets are host-bound; native read admission is explicit host policy."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.mcp.mcp_action import MCPAction


def remote_tool(name: str) -> Any:
    return SimpleNamespace(
        name=name,
        description=name,
        inputSchema={"type": "object", "properties": {}, "additionalProperties": True},
        annotations=SimpleNamespace(readOnlyHint=True),
    )


@pytest.mark.asyncio
async def test_arguments_cannot_redirect_the_checked_mcp_target() -> None:
    action = MCPAction(servers=[])
    client = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[], isError=False))
    )
    with (
        patch.object(MCPAction, "get_server_names", return_value=["safe"]),
        patch.object(
            MCPAction,
            "get_tools_cached",
            new=AsyncMock(return_value=[remote_tool("read")]),
        ),
        patch.object(MCPAction, "get_client", return_value=client) as select,
    ):
        tools = await action.get_tools()
        await tools[0].call(sn="privileged", tn="delete", query="sample")
    select.assert_called_once_with("safe")
    client.call_tool.assert_awaited_once_with(
        "read", {"sn": "privileged", "tn": "delete", "query": "sample"}
    )


@pytest.mark.asyncio
async def test_remote_read_hint_cannot_grant_native_execution() -> None:
    action = MCPAction(servers=[], native_read_tools=["mcp_safe__read"])
    with (
        patch.object(MCPAction, "get_server_names", return_value=["safe"]),
        patch.object(
            MCPAction,
            "get_tools_cached",
            new=AsyncMock(return_value=[remote_tool("read"), remote_tool("delete")]),
        ),
    ):
        tools = await action.get_tools()
    assert [(t.name, t.effect_class) for t in tools] == [
        ("mcp_safe__read", None),
        ("mcp_safe__delete", None),
    ]


@pytest.mark.asyncio
async def test_sanitized_tool_name_collisions_reject() -> None:
    action = MCPAction(servers=[])
    with (
        patch.object(MCPAction, "get_server_names", return_value=["safe"]),
        patch.object(
            MCPAction,
            "get_tools_cached",
            new=AsyncMock(return_value=[remote_tool("read"), remote_tool("read\n")]),
        ),
    ):
        with pytest.raises(ValueError, match="Duplicate surfaced"):
            await action.get_tools()
