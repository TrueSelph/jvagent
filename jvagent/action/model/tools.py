"""Function calling support for model actions.

Provides utilities for defining, validating, and parsing tool/function calls
in LLM interactions following the OpenAI function calling format.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolDefinition:
    """Represents a tool/function definition for LLM function calling.

    Follows OpenAI's function calling format, which is widely supported
    across different LLM providers.

    Examples:
        >>> tool = ToolDefinition(
        ...     name="get_weather",
        ...     description="Get current weather for a location",
        ...     parameters={
        ...         "type": "object",
        ...         "properties": {
        ...             "location": {"type": "string", "description": "City name"},
        ...             "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        ...         },
        ...         "required": ["location"]
        ...     }
        ... )
        >>> tool_dict = tool.to_dict()
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
    ):
        """Initialize a tool definition.

        Args:
            name: Function name (alphanumeric and underscores only)
            description: Human-readable description of what the function does
            parameters: JSON Schema for the function parameters
        """
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_dict(self) -> Dict[str, Any]:
        """Convert to OpenAI function calling format.

        Returns:
            Dictionary representation in OpenAI format
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolDefinition":
        """Create ToolDefinition from dictionary.

        Args:
            data: Dictionary in OpenAI function format

        Returns:
            ToolDefinition instance
        """
        func = data.get("function", {})
        return cls(
            name=func.get("name", ""),
            description=func.get("description", ""),
            parameters=func.get("parameters", {}),
        )

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate the tool definition.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check name
        if not self.name:
            return False, "Tool name is required"

        if not self.name.replace("_", "").isalnum():
            return False, "Tool name must be alphanumeric (with underscores)"

        # Check description
        if not self.description:
            return False, "Tool description is required"

        # Check parameters
        if not isinstance(self.parameters, dict):
            return False, "Parameters must be a dictionary"

        if "type" not in self.parameters:
            return False, "Parameters must have 'type' field"

        if self.parameters["type"] != "object":
            return False, "Parameters type must be 'object'"

        if "properties" not in self.parameters:
            return False, "Parameters must have 'properties' field"

        return True, None


class ToolCall:
    """Represents a tool/function call made by the LLM.

    Examples:
        >>> call = ToolCall(
        ...     id="call_abc123",
        ...     name="get_weather",
        ...     arguments={"location": "San Francisco", "unit": "celsius"}
        ... )
        >>> result = await execute_tool(call)
    """

    def __init__(
        self,
        id: str,
        name: str,
        arguments: Dict[str, Any],
    ):
        """Initialize a tool call.

        Args:
            id: Unique identifier for this call
            name: Function name
            arguments: Function arguments as dictionary
        """
        self.id = id
        self.name = name
        self.arguments = arguments

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }

    @classmethod
    def from_openai_format(cls, data: Dict[str, Any]) -> "ToolCall":
        """Parse from OpenAI function call format.

        Args:
            data: Tool call dict from OpenAI response

        Returns:
            ToolCall instance
        """
        function = data.get("function", {})

        # Parse arguments (may be JSON string)
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse tool call arguments: {arguments}")
                arguments = {}

        return cls(
            id=data.get("id", ""),
            name=function.get("name", ""),
            arguments=arguments,
        )


class ToolManager:
    """Manages tools for model actions.

    Provides utilities for registering tools, validating definitions,
    and parsing function calls from LLM responses.

    Examples:
        >>> manager = ToolManager()
        >>> manager.register_tool(
        ...     name="get_weather",
        ...     description="Get weather",
        ...     parameters={...}
        ... )
        >>> tools = manager.get_tools_list()
        >>> result = await model.query("What's the weather?", tools=tools)
    """

    def __init__(self):
        """Initialize tool manager."""
        self.tools: Dict[str, ToolDefinition] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
    ) -> ToolDefinition:
        """Register a tool definition.

        Args:
            name: Function name
            description: Function description
            parameters: JSON Schema for parameters

        Returns:
            ToolDefinition instance

        Raises:
            ValueError: If tool definition is invalid
        """
        tool = ToolDefinition(name, description, parameters)

        # Validate
        is_valid, error = tool.validate()
        if not is_valid:
            raise ValueError(f"Invalid tool definition: {error}")

        self.tools[name] = tool
        logger.debug(f"Registered tool: {name}")

        return tool

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool.

        Args:
            name: Tool name to remove

        Returns:
            True if tool was removed, False if not found
        """
        if name in self.tools:
            del self.tools[name]
            logger.debug(f"Unregistered tool: {name}")
            return True
        return False

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool definition by name.

        Args:
            name: Tool name

        Returns:
            ToolDefinition if found, None otherwise
        """
        return self.tools.get(name)

    def get_tools_list(self) -> List[Dict[str, Any]]:
        """Get list of all tool definitions in OpenAI format.

        Returns:
            List of tool definition dictionaries
        """
        return [tool.to_dict() for tool in self.tools.values()]

    def parse_tool_calls(self, tool_calls_data: List[Dict[str, Any]]) -> List[ToolCall]:
        """Parse tool calls from LLM response.

        Args:
            tool_calls_data: List of tool call dicts from LLM response

        Returns:
            List of ToolCall instances
        """
        calls = []
        for data in tool_calls_data:
            try:
                call = ToolCall.from_openai_format(data)
                calls.append(call)
            except Exception as e:
                logger.warning(f"Failed to parse tool call: {e}")
                continue

        return calls

    def validate_tool_call(self, call: ToolCall) -> tuple[bool, Optional[str]]:
        """Validate a tool call against registered tools.

        Args:
            call: ToolCall to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if tool is registered
        tool = self.get_tool(call.name)
        if not tool:
            return False, f"Tool '{call.name}' is not registered"

        # Validate arguments against schema
        # Note: Full JSON Schema validation would require jsonschema library
        # For now, we do basic validation

        required = tool.parameters.get("required", [])
        properties = tool.parameters.get("properties", {})

        # Check required parameters
        for param in required:
            if param not in call.arguments:
                return False, f"Missing required parameter: {param}"

        # Check for unknown parameters
        for param in call.arguments:
            if param not in properties:
                return False, f"Unknown parameter: {param}"

        return True, None


# ============================================================================
# Example Tool Definitions
# ============================================================================


def create_weather_tool() -> ToolDefinition:
    """Create an example weather tool definition.

    Returns:
        ToolDefinition for weather lookup
    """
    return ToolDefinition(
        name="get_current_weather",
        description="Get the current weather in a given location",
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA",
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "The temperature unit to use",
                },
            },
            "required": ["location"],
        },
    )


def create_calculator_tool() -> ToolDefinition:
    """Create an example calculator tool definition.

    Returns:
        ToolDefinition for basic calculations
    """
    return ToolDefinition(
        name="calculate",
        description="Perform a mathematical calculation",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate (e.g., '2 + 2', '10 * 5')",
                },
            },
            "required": ["expression"],
        },
    )


def create_search_tool() -> ToolDefinition:
    """Create an example search tool definition.

    Returns:
        ToolDefinition for web search
    """
    return ToolDefinition(
        name="web_search",
        description="Search the web for information",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )
