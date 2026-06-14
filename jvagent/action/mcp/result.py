"""MCPFulfillResult for MCPAction.fulfill() return type."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class MCPFulfillResult:
    """Result of MCPAction.fulfill() for callers.

    Attributes:
        text: Primary human-readable content from the MCP tool result.
        structured: Optional structured content from the MCP result for callers.
        is_error: True if the tool or gateway reported an error.
        error_kind: Optional category for caller branching (e.g. no_tool, tool_failed, gateway_error).
        tool_name: Optional name of the tool that was called (for debugging).
        raw_content: Optional raw content list from MCP (for debugging).
    """

    text: str
    structured: Optional[Dict[str, Any]] = None
    is_error: bool = False
    error_kind: Optional[str] = None
    tool_name: Optional[str] = None
    raw_content: Optional[List[Any]] = None
