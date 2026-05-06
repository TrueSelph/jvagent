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
        # Validate against strict-provider rules (OpenAI gpt-4.1 etc.) so a
        # malformed schema fails fast at construction rather than at first
        # model call. We log + tolerate rather than raise here so a single
        # bad tool can't take down the whole agent boot — the tool will
        # still serialize and may fail downstream, but at least the
        # diagnostic appears immediately and labels the offending tool.
        try:
            from jvagent.tooling.tool_schema_validator import (
                validate_parameters_schema,
            )

            issues = validate_parameters_schema(self.parameters_schema)
            if issues:
                import logging as _logging

                _log = _logging.getLogger(__name__)
                for path, msg in issues:
                    _log.warning(
                        "Tool %r has invalid parameters_schema at %s: %s",
                        self.name,
                        path,
                        msg,
                    )
        except Exception:
            # Validator import failed (e.g. circular) — skip silently.
            pass

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
