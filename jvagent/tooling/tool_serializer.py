from typing import Any, Dict, List

from jvagent.tooling.tool import Tool


class ToolSerializer:
    """Converts ``Tool`` instances into provider-specific wire formats.

    The default implementation produces OpenAI-compatible function-calling
    dicts.  Subclass or extend for provider-specific serialization (e.g.
    Anthropic tool-use blocks).
    """

    @staticmethod
    def to_openai_function_format(tool: Tool) -> Dict[str, Any]:
        return tool.to_serialized()

    @staticmethod
    def serialize(tool: Tool) -> Dict[str, Any]:
        return ToolSerializer.to_openai_function_format(tool)

    @classmethod
    def serialize_all(cls, tools: List[Tool]) -> List[Dict[str, Any]]:
        return [cls.serialize(t) for t in tools]
