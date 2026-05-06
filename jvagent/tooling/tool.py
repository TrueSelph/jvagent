import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from jvagent.tooling.tool_result import ToolResult


@dataclass
class Tool:
    """Provider-agnostic tool definition that wraps an executable callable.

    A ``Tool`` bundles metadata (name, description, JSON Schema parameters) with
    an async dispatch function that accepts keyword arguments and returns a
    ``ToolResult``.  Each Action exposes its capabilities as ``Tool`` instances;
    harness services expose themselves as tools so the cockpit model can call them.
    """

    name: str
    description: str
    parameters_schema: Dict[str, Any] = field(default_factory=dict)
    execute: Callable[..., Any] = field(default=lambda **_: ToolResult(""))

    def __post_init__(self) -> None:
        if not self.parameters_schema:
            self.parameters_schema = {"type": "object", "properties": {}}

    async def call(self, **kwargs: Any) -> ToolResult:
        result = self.execute(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            return ToolResult(content=result)
        if isinstance(result, ToolResult):
            return result
        import json

        return ToolResult(content=json.dumps(result) if result is not None else "")

    async def is_available(self) -> bool:
        return True

    def to_serialized(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
