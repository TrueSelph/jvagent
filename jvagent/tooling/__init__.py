from importlib import import_module
from typing import TYPE_CHECKING, Any

from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_observability import (
    SkillActivationEnvelope,
    ToolExecutionEnvelope,
)
from jvagent.tooling.tool_registry import ToolRegistry
from jvagent.tooling.tool_result import ToolResult
from jvagent.tooling.tool_serializer import ToolSerializer

if TYPE_CHECKING:
    from jvagent.tooling.tool_executor import ToolExecutionEngine


def __getattr__(name: str) -> Any:
    """Retain the old executor import without loading it for native tool contracts."""
    if name == "ToolExecutionEngine":
        value = getattr(import_module("jvagent.tooling.tool_executor"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutionEngine",
    "ToolSerializer",
    "ToolExecutionEnvelope",
    "SkillActivationEnvelope",
]
