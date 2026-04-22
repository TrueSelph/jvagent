"""Tests for MCPAction multi-server selection and filtering."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.mcp.mcp_action import MCPAction, _parse_tool_selection


def _tool(name: str):
    tool = MagicMock()
    tool.name = name
    tool.description = f"{name} description"
    tool.input_schema = {"type": "object", "properties": {}}
    return tool


def test_strip_trailing_path_keeps_scoped_npm_package():
    """Regression: do not strip @scope/pkg — npx would treat the only path as cwd/package."""
    action = MCPAction(servers=[])
    base = ["-y", "@modelcontextprotocol/server-filesystem"]
    assert action._strip_trailing_path_arg(base) == base
    with_dot = list(base) + ["."]
    assert action._strip_trailing_path_arg(with_dot) == base


class TestMCPActionFiltering:
    @pytest.mark.asyncio
    async def test_filter_tools_all_with_denied_patterns(self):
        action = MCPAction(
            sandbox_mode=False,
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "tools": "-all",
                    "denied_tools": ["delete_*"],
                }
            ],
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()
        entry = action._servers_by_name["filesystem"]

        filtered = action._filter_tools(
            entry, [_tool("read_file"), _tool("delete_file"), _tool("list_files")]
        )
        assert [t.name for t in filtered] == ["read_file", "list_files"]

    @pytest.mark.asyncio
    async def test_filter_tools_allow_list_and_globs(self):
        action = MCPAction(
            sandbox_mode=False,
            servers=[
                {
                    "name": "filesystem",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "tools": ["read_*", "list_files"],
                    "denied_tools": [],
                }
            ],
        )
        with patch.object(MCPAction, "get_agent", new=AsyncMock(return_value=None)):
            await action._build_server_entries()
        entry = action._servers_by_name["filesystem"]

        filtered = action._filter_tools(
            entry,
            [_tool("read_file"), _tool("list_files"), _tool("write_file")],
        )
        assert [t.name for t in filtered] == ["read_file", "list_files"]


class TestMCPActionFulfill:
    @pytest.mark.asyncio
    async def test_fulfill_selects_server_and_tool(self):
        action = MCPAction(servers=[])
        inventory_tool = _tool("read_file")
        action._resolve_tool_inventory = AsyncMock(
            return_value=[("filesystem", inventory_tool)]
        )

        model_result = MagicMock()
        model_result.get_response = AsyncMock(
            return_value='{"server_name":"filesystem","tool_name":"read_file","arguments":{"path":"README.md"}}'
        )
        model_action = MagicMock()
        model_action.query_sync = AsyncMock(return_value=model_result)

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "contents"
        call_result = MagicMock()
        call_result.isError = False
        call_result.content = [text_item]
        client = MagicMock()
        client.call_tool = AsyncMock(return_value=call_result)
        with (
            patch.object(
                MCPAction, "get_model_action", new=AsyncMock(return_value=model_action)
            ),
            patch.object(
                MCPAction,
                "get_client_for_user",
                new=AsyncMock(return_value=client),
            ),
        ):
            result = await action.fulfill("read README.md", user_id="user-123")

        assert result.is_error is False
        assert result.text == "contents"
        client.call_tool.assert_awaited_once_with("read_file", {"path": "README.md"})

    @pytest.mark.asyncio
    async def test_fulfill_rejects_invalid_server_tool_pair(self):
        action = MCPAction(servers=[])
        action._resolve_tool_inventory = AsyncMock(
            return_value=[("filesystem", _tool("read_file"))]
        )

        model_result = MagicMock()
        model_result.get_response = AsyncMock(
            return_value='{"server_name":"websearch","tool_name":"search","arguments":{}}'
        )
        model_action = MagicMock()
        model_action.query_sync = AsyncMock(return_value=model_result)
        with patch.object(
            MCPAction, "get_model_action", new=AsyncMock(return_value=model_action)
        ):
            result = await action.fulfill("search the web")

        assert result.is_error is True
        assert result.error_kind == "gateway_error"
        assert "Invalid server/tool selection" in result.text


def test_parse_tool_selection_reads_server_name():
    parsed = _parse_tool_selection(
        '```json\n{"server_name":"filesystem","tool_name":"read_file","arguments":{"path":"a.txt"}}\n```'
    )
    assert parsed == {
        "server_name": "filesystem",
        "tool_name": "read_file",
        "arguments": {"path": "a.txt"},
    }
