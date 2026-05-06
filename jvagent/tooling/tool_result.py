from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ToolResult:
    """Result of a single tool execution.

    Attributes:
        content: The text content to feed back to the language model.
        is_error: Whether the tool call failed.
        metadata: Arbitrary key-value payload (call id, latency, etc.).
    """

    content: str
    is_error: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def tool_result_message(self) -> Dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.metadata.get("tool_call_id", ""),
            "content": self.content,
        }

    @classmethod
    def error(cls, message: str, tool_call_id: str = "") -> "ToolResult":
        return cls(
            content=f"Error: {message}",
            is_error=True,
            metadata={"tool_call_id": tool_call_id},
        )

    @classmethod
    def empty(cls, tool_name: str, tool_call_id: str = "") -> "ToolResult":
        return cls(
            content=f"Tool `{tool_name}` returned empty output.",
            metadata={"tool_call_id": tool_call_id},
        )
