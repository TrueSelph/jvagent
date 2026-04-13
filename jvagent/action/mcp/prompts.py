"""Prompt template for NL -> tool name + arguments selection."""

TOOL_SELECTION_SYSTEM = """You are a tool selector. Given a user request and a list of MCP tools (name, description, inputSchema), output exactly one tool name and a JSON object of arguments to call it with.
Output only valid JSON in this format, with no other text:
{"tool_name": "<name>", "arguments": {<key-value pairs for the tool's inputSchema>}}
If no tool fits the request, use: {"tool_name": "", "arguments": {}}
"""

TOOL_SELECTION_USER_TEMPLATE = """User request: {natural_language_command}

Available tools (name, description, inputSchema):
{tools_description}

Respond with exactly one JSON object: {"tool_name": "...", "arguments": {...}}
"""


def build_tool_selection_prompt(
    natural_language_command: str, tools_description: str
) -> str:
    """Build the user prompt for tool selection.

    Args:
        natural_language_command: The user's natural language request.
        tools_description: Formatted string of tool name, description, and inputSchema per tool.

    Returns:
        User prompt string.
    """
    return TOOL_SELECTION_USER_TEMPLATE.format(
        natural_language_command=natural_language_command,
        tools_description=tools_description or "(no tools available)",
    )
