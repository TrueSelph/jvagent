"""Tests for ToolExecutor: tool registration, dispatch, and error handling."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.model.language.tools import ToolDefinition, ToolManager
from jvagent.action.skill.tool_executor import ToolDispatchError, ToolExecutor


class TestToolExecutorRegistration:
    """Test tool registration and initialization."""

    def test_register_local_tool(self):
        executor = ToolExecutor()
        handler = AsyncMock(return_value="result")
        tool_def = executor.register_local_tool(
            name="test_tool",
            handler=handler,
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
        )
        assert tool_def.name == "test_tool"
        assert "test_tool" in executor.get_tool_names()
        assert len(executor.get_tools_list()) == 1

    def test_get_tools_list_returns_openai_format(self):
        executor = ToolExecutor()
        executor.register_local_tool(
            name="weather",
            handler=AsyncMock(),
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        tools = executor.get_tools_list()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "weather"

    def test_apply_allowed_patterns(self):
        executor = ToolExecutor()
        executor.register_local_tool(
            name="read_file",
            handler=AsyncMock(),
            description="Read",
            parameters={"type": "object", "properties": {}},
        )
        executor.register_local_tool(
            name="write_file",
            handler=AsyncMock(),
            description="Write",
            parameters={"type": "object", "properties": {}},
        )
        executor.register_local_tool(
            name="search",
            handler=AsyncMock(),
            description="Search",
            parameters={"type": "object", "properties": {}},
        )
        executor._apply_pattern_filters(allowed_patterns=["read_*", "search"])
        assert executor.get_tool_names() == {"read_file", "search"}

    def test_apply_denied_patterns(self):
        executor = ToolExecutor()
        executor.register_local_tool(
            name="read_file",
            handler=AsyncMock(),
            description="Read",
            parameters={"type": "object", "properties": {}},
        )
        executor.register_local_tool(
            name="delete_file",
            handler=AsyncMock(),
            description="Delete",
            parameters={"type": "object", "properties": {}},
        )
        executor._apply_pattern_filters(denied_patterns=["delete_*"])
        assert executor.get_tool_names() == {"read_file"}

    @pytest.mark.asyncio
    async def test_register_and_activate_skill_bundle(self, tmp_path):
        executor = ToolExecutor(validate_calls=False)
        skill_dir = tmp_path / "skills" / "example_skill"
        skill_dir.mkdir(parents=True)
        tool_file = skill_dir / "echo_tool.py"
        tool_file.write_text(
            """
def get_tool_definition():
    return {
        "name": "echo_tool",
        "description": "Echo back a value",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"]
        }
    }

async def execute(arguments):
    return arguments.get("value", "")
""",
            encoding="utf-8",
        )

        executor.register_skill_bundle(
            skill_name="example_skill",
            dir_path=str(skill_dir),
            tool_files=[str(tool_file)],
            allowed_tools=["echo_tool"],
        )
        assert "example_skill__echo_tool" not in executor.get_tool_names()

        activated = await executor.activate_skill("example_skill")
        assert activated == ["example_skill__echo_tool"]
        assert "example_skill__echo_tool" in executor.get_tool_names()


class TestToolExecutorDispatch:
    """Test tool call dispatching."""

    @pytest.mark.asyncio
    async def test_dispatch_local_tool(self):
        handler = AsyncMock(return_value="42")
        executor = ToolExecutor(validate_calls=False)
        executor.register_local_tool(
            name="calculate",
            handler=handler,
            description="Calculate",
            parameters={
                "type": "object",
                "properties": {"expr": {"type": "string"}},
                "required": ["expr"],
            },
        )

        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calculate", "arguments": {"expr": "2+2"}},
            }
        ]
        results = await executor.dispatch(tool_calls)
        assert len(results) == 1
        assert results[0]["role"] == "tool"
        assert results[0]["tool_call_id"] == "call_1"
        assert results[0]["content"] == "42"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_returns_error(self):
        executor = ToolExecutor()
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "nonexistent", "arguments": {}},
            }
        ]
        results = await executor.dispatch(tool_calls)
        assert len(results) == 1
        assert "Error:" in results[0]["content"]
        assert "not registered" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_dispatch_validation_failure(self):
        executor = ToolExecutor(validate_calls=True)
        executor.register_local_tool(
            name="needs_input",
            handler=AsyncMock(),
            description="Needs input",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        # Missing required param
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "needs_input", "arguments": {}},
            }
        ]
        results = await executor.dispatch(tool_calls)
        assert "Error:" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_dispatch_timeout(self):
        async def slow_handler(args):
            await asyncio.sleep(10)
            return "done"

        executor = ToolExecutor(call_timeout=0.1, validate_calls=False)
        executor.register_local_tool(
            name="slow_tool",
            handler=slow_handler,
            description="Slow",
            parameters={"type": "object", "properties": {}},
        )
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "slow_tool", "arguments": {}},
            }
        ]
        results = await executor.dispatch(tool_calls)
        assert "timed out" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_dispatch_sanitize_errors(self):
        async def failing_handler(args):
            raise RuntimeError("internal secret error trace")

        executor = ToolExecutor(sanitize_errors=True, validate_calls=False)
        executor.register_local_tool(
            name="failing",
            handler=failing_handler,
            description="Failing",
            parameters={"type": "object", "properties": {}},
        )
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "failing", "arguments": {}},
            }
        ]
        results = await executor.dispatch(tool_calls)
        assert "secret" not in results[0]["content"]
        assert "Tool execution failed" in results[0]["content"]

    @pytest.mark.asyncio
    async def test_dispatch_multiple_tools_concurrently(self):
        call_order = []

        async def handler_a(args):
            await asyncio.sleep(0.05)
            call_order.append("a")
            return "result_a"

        async def handler_b(args):
            call_order.append("b")
            return "result_b"

        executor = ToolExecutor(validate_calls=False, max_concurrent_calls=5)
        executor.register_local_tool(
            name="tool_a",
            handler=handler_a,
            description="A",
            parameters={"type": "object", "properties": {}},
        )
        executor.register_local_tool(
            name="tool_b",
            handler=handler_b,
            description="B",
            parameters={"type": "object", "properties": {}},
        )

        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "tool_a", "arguments": {}},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "tool_b", "arguments": {}},
            },
        ]
        results = await executor.dispatch(tool_calls)
        assert len(results) == 2
        # B should complete before A due to shorter sleep
        assert "b" in call_order
        assert "a" in call_order


class TestToolExecutorMCP:
    """Test MCP tool registration."""

    @pytest.mark.asyncio
    async def test_register_mcp_server(self):
        from jvagent.action.mcp.mcp_action import MCPAction

        executor = ToolExecutor()

        # Mock visitor and agent
        visitor = MagicMock()
        mock_agent = AsyncMock()
        visitor._agent = mock_agent

        # Mock MCP action
        mock_mcp = MagicMock(spec=MCPAction)
        mock_mcp.enabled = True
        mock_mcp.get_server_names = MagicMock(return_value=["filesystem"])
        mock_tool = MagicMock()
        mock_tool.name = "fs_read"
        mock_tool.description = "Read a file"
        mock_tool.input_schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        mock_mcp.get_tools_cached = AsyncMock(return_value=[mock_tool])
        mock_mcp.get_client = MagicMock(return_value=MagicMock())
        # get_actions() returns list of all actions
        mock_agent.get_actions = AsyncMock(return_value=[mock_mcp])

        await executor._register_mcp_server(visitor, "filesystem")

        assert "fs_read" in executor.get_tool_names()
        assert executor._handlers["fs_read"][0] == "mcp"

    @pytest.mark.asyncio
    async def test_register_mcp_server_not_found(self):
        executor = ToolExecutor()
        visitor = MagicMock()
        mock_agent = AsyncMock()
        visitor._agent = mock_agent
        # get_actions() returns empty list
        mock_agent.get_actions = AsyncMock(return_value=[])

        # Should not raise, just warn
        await executor._register_mcp_server(visitor, "nonexistent")
        assert len(executor.get_tool_names()) == 0

    @pytest.mark.asyncio
    async def test_dispatch_mcp_tool_success(self):
        executor = ToolExecutor(validate_calls=False)
        call = MagicMock()
        call.name = "fs_read"
        call.arguments = {"path": "/tmp/test"}
        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = "ok"
        call_result = MagicMock()
        call_result.is_error = False
        call_result.content = [text_item]

        client = MagicMock()
        client.call_tool = AsyncMock(return_value=call_result)
        mcp_action = MagicMock()
        mcp_action.get_client = MagicMock(return_value=client)

        result = await executor._dispatch_mcp_tool(call, (mcp_action, "filesystem"))
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_dispatch_mcp_tool_handles_camelcase_is_error(self):
        """Regression: real MCP SDK CallToolResult uses camelCase ``isError``.

        Previously _normalize_call_result() looked for snake_case and
        defaulted is_error to True, causing every successful MCP tool call
        (e.g. ``list_allowed_directories``) to surface as a sanitized
        ``Tool execution failed: <name>`` error to the LLM.
        """
        from pydantic import BaseModel

        class _StubContent(BaseModel):
            type: str = "text"
            text: str = ""

        class _StubCallResult(BaseModel):
            content: list[_StubContent] = []
            isError: bool = False
            structuredContent: dict | None = None

        executor = ToolExecutor(validate_calls=False)
        call = MagicMock()
        call.name = "list_allowed_directories"
        call.arguments = {}
        call_result = _StubCallResult(
            content=[_StubContent(type="text", text="/tmp\n/var")],
            isError=False,
        )

        client = MagicMock()
        client.call_tool = AsyncMock(return_value=call_result)
        mcp_action = MagicMock()
        mcp_action.get_client = MagicMock(return_value=client)

        result = await executor._dispatch_mcp_tool(call, (mcp_action, "filesystem"))
        assert result == "/tmp\n/var"
