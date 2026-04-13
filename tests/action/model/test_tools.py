"""Tests for model action tools."""

import pytest

from jvagent.action.model.language.tools import (
    ToolCall,
    ToolDefinition,
    ToolManager,
    create_calculator_tool,
    create_weather_tool,
)


class TestToolDefinition:
    """Tests for ToolDefinition class."""

    def test_init(self):
        """Test tool definition initialization."""
        tool = ToolDefinition(
            name="test_function",
            description="A test function",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                },
            },
        )

        assert tool.name == "test_function"
        assert tool.description == "A test function"
        assert tool.parameters["type"] == "object"

    def test_to_dict(self):
        """Test converting to OpenAI format."""
        tool = ToolDefinition(
            name="test_function",
            description="A test function",
            parameters={
                "type": "object",
                "properties": {},
            },
        )

        data = tool.to_dict()

        assert data["type"] == "function"
        assert data["function"]["name"] == "test_function"
        assert data["function"]["description"] == "A test function"

    def test_from_dict(self):
        """Test creating from dictionary."""
        data = {
            "type": "function",
            "function": {
                "name": "test_function",
                "description": "A test function",
                "parameters": {"type": "object", "properties": {}},
            },
        }

        tool = ToolDefinition.from_dict(data)

        assert tool.name == "test_function"
        assert tool.description == "A test function"

    def test_validate_valid(self):
        """Test validating a valid tool definition."""
        tool = ToolDefinition(
            name="valid_function",
            description="A valid function",
            parameters={
                "type": "object",
                "properties": {"arg1": {"type": "string"}},
            },
        )

        is_valid, error = tool.validate()

        assert is_valid
        assert error is None

    def test_validate_invalid_name(self):
        """Test validating tool with invalid name."""
        tool = ToolDefinition(
            name="",
            description="A function",
            parameters={"type": "object", "properties": {}},
        )

        is_valid, error = tool.validate()

        assert not is_valid
        assert "name is required" in error

    def test_validate_invalid_type(self):
        """Test validating tool with invalid parameter type."""
        tool = ToolDefinition(
            name="function",
            description="A function",
            parameters={"properties": {}},  # Missing 'type'
        )

        is_valid, error = tool.validate()

        assert not is_valid
        assert "type" in error


class TestToolCall:
    """Tests for ToolCall class."""

    def test_init(self):
        """Test tool call initialization."""
        call = ToolCall(
            id="call_123",
            name="test_function",
            arguments={"arg1": "value1"},
        )

        assert call.id == "call_123"
        assert call.name == "test_function"
        assert call.arguments["arg1"] == "value1"

    def test_to_dict(self):
        """Test converting to dictionary."""
        call = ToolCall(
            id="call_123",
            name="test_function",
            arguments={"arg1": "value1"},
        )

        data = call.to_dict()

        assert data["id"] == "call_123"
        assert data["name"] == "test_function"
        assert data["arguments"]["arg1"] == "value1"

    def test_from_openai_format(self):
        """Test parsing from OpenAI format."""
        data = {
            "id": "call_123",
            "function": {
                "name": "test_function",
                "arguments": '{"arg1": "value1"}',
            },
        }

        call = ToolCall.from_openai_format(data)

        assert call.id == "call_123"
        assert call.name == "test_function"
        assert call.arguments["arg1"] == "value1"

    def test_from_openai_format_dict_args(self):
        """Test parsing when arguments are already a dict."""
        data = {
            "id": "call_123",
            "function": {
                "name": "test_function",
                "arguments": {"arg1": "value1"},
            },
        }

        call = ToolCall.from_openai_format(data)

        assert call.arguments["arg1"] == "value1"


class TestToolManager:
    """Tests for ToolManager class."""

    def test_register_tool(self):
        """Test registering a tool."""
        manager = ToolManager()

        tool = manager.register_tool(
            name="test_function",
            description="A test function",
            parameters={
                "type": "object",
                "properties": {"arg1": {"type": "string"}},
            },
        )

        assert tool.name == "test_function"
        assert "test_function" in manager.tools

    def test_register_invalid_tool(self):
        """Test registering an invalid tool raises error."""
        manager = ToolManager()

        with pytest.raises(ValueError):
            manager.register_tool(
                name="",  # Invalid: empty name
                description="A function",
                parameters={"type": "object", "properties": {}},
            )

    def test_unregister_tool(self):
        """Test unregistering a tool."""
        manager = ToolManager()

        manager.register_tool(
            name="test_function",
            description="A test function",
            parameters={"type": "object", "properties": {}},
        )

        result = manager.unregister_tool("test_function")

        assert result
        assert "test_function" not in manager.tools

    def test_unregister_nonexistent_tool(self):
        """Test unregistering a non-existent tool."""
        manager = ToolManager()

        result = manager.unregister_tool("nonexistent")

        assert not result

    def test_get_tool(self):
        """Test getting a tool by name."""
        manager = ToolManager()

        manager.register_tool(
            name="test_function",
            description="A test function",
            parameters={"type": "object", "properties": {}},
        )

        tool = manager.get_tool("test_function")

        assert tool is not None
        assert tool.name == "test_function"

    def test_get_tools_list(self):
        """Test getting list of all tools."""
        manager = ToolManager()

        manager.register_tool(
            name="function1",
            description="Function 1",
            parameters={"type": "object", "properties": {}},
        )
        manager.register_tool(
            name="function2",
            description="Function 2",
            parameters={"type": "object", "properties": {}},
        )

        tools = manager.get_tools_list()

        assert len(tools) == 2
        assert all(t["type"] == "function" for t in tools)

    def test_parse_tool_calls(self):
        """Test parsing tool calls from response."""
        manager = ToolManager()

        tool_calls_data = [
            {
                "id": "call_1",
                "function": {
                    "name": "function1",
                    "arguments": '{"arg": "value"}',
                },
            },
            {
                "id": "call_2",
                "function": {
                    "name": "function2",
                    "arguments": '{"arg": "value2"}',
                },
            },
        ]

        calls = manager.parse_tool_calls(tool_calls_data)

        assert len(calls) == 2
        assert calls[0].name == "function1"
        assert calls[1].name == "function2"

    def test_validate_tool_call_valid(self):
        """Test validating a valid tool call."""
        manager = ToolManager()

        manager.register_tool(
            name="test_function",
            description="A test function",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                    "arg2": {"type": "number"},
                },
                "required": ["arg1"],
            },
        )

        call = ToolCall(
            id="call_1",
            name="test_function",
            arguments={"arg1": "value1", "arg2": 42},
        )

        is_valid, error = manager.validate_tool_call(call)

        assert is_valid
        assert error is None

    def test_validate_tool_call_missing_required(self):
        """Test validating tool call with missing required parameter."""
        manager = ToolManager()

        manager.register_tool(
            name="test_function",
            description="A test function",
            parameters={
                "type": "object",
                "properties": {
                    "arg1": {"type": "string"},
                },
                "required": ["arg1"],
            },
        )

        call = ToolCall(
            id="call_1",
            name="test_function",
            arguments={},  # Missing required arg1
        )

        is_valid, error = manager.validate_tool_call(call)

        assert not is_valid
        assert "required parameter" in error.lower()

    def test_validate_tool_call_unregistered(self):
        """Test validating call to unregistered tool."""
        manager = ToolManager()

        call = ToolCall(
            id="call_1",
            name="nonexistent_function",
            arguments={},
        )

        is_valid, error = manager.validate_tool_call(call)

        assert not is_valid
        assert "not registered" in error


class TestExampleTools:
    """Tests for example tool definitions."""

    def test_create_weather_tool(self):
        """Test creating weather tool."""
        tool = create_weather_tool()

        assert tool.name == "get_current_weather"
        assert "weather" in tool.description.lower()

        is_valid, _ = tool.validate()
        assert is_valid

    def test_create_calculator_tool(self):
        """Test creating calculator tool."""
        tool = create_calculator_tool()

        assert tool.name == "calculate"
        assert "mathematical" in tool.description.lower()

        is_valid, _ = tool.validate()
        assert is_valid
