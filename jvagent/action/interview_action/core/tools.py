"""Tool builder for InterviewAction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ..interview_action import InterviewAction

from jvagent.tooling.tool import Tool

from .interview_loader import InterviewSpec, SkillToolDef

logger = logging.getLogger(__name__)


def skill_tool_name(spec: InterviewSpec, tool_name: str) -> str:
    return f"{spec.name}__{tool_name}"


def _build_json_schema_from_params(params: Dict[str, Any]) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for pname, pdef in params.items():
        if isinstance(pdef, dict):
            prop = dict(pdef)
            if "type" not in prop:
                prop["type"] = "string"
            schema["properties"][pname] = prop
            if pdef.get("required", False):
                schema["required"].append(pname)
        else:
            schema["properties"][pname] = {"type": "string", "description": str(pdef)}
    if not schema["required"]:
        del schema["required"]
    return schema


def build_tools(action: "InterviewAction") -> List[Tool]:
    tools: List[Tool] = []
    tools.extend(_build_data_tools(action))
    tools.extend(_build_custom_tools(action))
    return tools


def _build_data_tools(action: "InterviewAction") -> List[Tool]:
    tools: List[Tool] = []

    async def _set_fields(
        fields: Dict[str, str] | None = None,
        field: str = "",
        value: str = "",
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        return await action._handle_set_fields(
            fields=fields, field=field, value=value, visitor=visitor, **kwargs
        )

    tools.append(
        Tool(
            name="interview__set_fields",
            description=(
                "Requires an active interview session (open with use_skill on the "
                "matching skill first). Validate and store one or more interview "
                "field values. Use for new answers, corrections to previously stored "
                "fields, or batch extraction from the user's latest message. "
                "Validators run per field inside this tool — never call validator "
                "functions directly. ok:false means a field was not stored. On ok:true, "
                "runs post_processor hooks when configured. Chain interview__next_question "
                "or interview__review per the SKILL procedure."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": (
                            "Map of field name to value (e.g. "
                            '{"user_name": "Jane Doe", "user_email": "jane@example.com"}).'
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "field": {
                        "type": "string",
                        "description": "Single-field alias for fields — prefer fields map.",
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "Value for field alias; user's latest utterance is validated "
                            "when value is omitted for a single field."
                        ),
                    },
                },
            },
            execute=_set_fields,
        )
    )

    async def _set_field(
        field: str = "", value: str = "", visitor: Any = None, **kwargs: Any
    ) -> str:
        return await action._handle_set_fields(
            field=field, value=value, visitor=visitor, **kwargs
        )

    tools.append(
        Tool(
            name="interview__set_field",
            description=(
                "Deprecated alias for interview__set_fields — pass field and value. "
                "Prefer interview__set_fields with a fields map."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["field"],
            },
            execute=_set_field,
        )
    )

    async def _get_fields(
        fields: List[str] | None = None,
        field: str = "",
        visitor: Any = None,
    ) -> str:
        return await action._handle_get_fields(
            fields=fields, field=field, visitor=visitor
        )

    tools.append(
        Tool(
            name="interview__get_fields",
            description=(
                "Read stored values for named fields. Omit fields (or pass empty list) "
                "to return all collected fields."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Field names to read; omit for all collected.",
                    },
                    "field": {
                        "type": "string",
                        "description": "Single-field alias — prefer fields array.",
                    },
                },
            },
            execute=_get_fields,
        )
    )

    async def _get_field(field: str, visitor: Any = None) -> str:
        return await action._handle_get_field(field, visitor)

    tools.append(
        Tool(
            name="interview__get_field",
            description="Deprecated alias for interview__get_fields — single field.",
            parameters_schema={
                "type": "object",
                "properties": {"field": {"type": "string"}},
                "required": ["field"],
            },
            execute=_get_field,
        )
    )

    async def _skip_field(field: str, visitor: Any = None) -> str:
        return await action._handle_skip_field(field, visitor)

    tools.append(
        Tool(
            name="interview__skip_field",
            description=(
                "Mark an optional field as skipped. Follow response_directive; "
                "call interview__next_question when the response chains mechanically."
            ),
            parameters_schema={
                "type": "object",
                "properties": {"field": {"type": "string"}},
                "required": ["field"],
            },
            execute=_skip_field,
        )
    )

    async def _next_question(visitor: Any = None) -> str:
        return await action._handle_next_question(visitor)

    tools.append(
        Tool(
            name="interview__next_question",
            description=(
                "Requires an active interview session (open with use_skill on the "
                "matching skill first). Get the next question(s). Runs pre_processor "
                "hooks for context. Returns next_questions, missing_required, and "
                "response_directive."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_next_question,
        )
    )

    async def _get_status(visitor: Any = None) -> str:
        return await action._handle_get_status(visitor)

    tools.append(
        Tool(
            name="interview__get_status",
            description=(
                "Full status of the active interview: fields, missing_required, "
                "skipped_fields, and session status."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_get_status,
        )
    )

    async def _review(visitor: Any = None) -> str:
        return await action._handle_review(visitor)

    tools.append(
        Tool(
            name="interview__review",
            description="Formatted review summary before completion.",
            parameters_schema={"type": "object", "properties": {}},
            execute=_review,
        )
    )

    async def _complete(visitor: Any = None) -> str:
        return await action._handle_complete(visitor)

    tools.append(
        Tool(
            name="interview__complete",
            description="Complete the interview after user confirms review.",
            parameters_schema={"type": "object", "properties": {}},
            execute=_complete,
        )
    )

    async def _cancel(visitor: Any = None) -> str:
        return await action._handle_cancel(visitor)

    tools.append(
        Tool(
            name="interview__cancel",
            description=(
                "Cancel and close the active interview session. Use when the user "
                "wants to stop, quit, or cancel — not when they want to start over."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_cancel,
        )
    )

    async def _reset(visitor: Any = None) -> str:
        return await action._handle_reset(visitor)

    tools.append(
        Tool(
            name="interview__reset",
            description=(
                "Clear progress and restart the active interview from the first "
                "question, or run the skill's custom reset handler when "
                "handlers.reset is declared. Use for start-over intent — "
                "not for cancel/stop/quit. Follow response_directive."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_reset,
        )
    )

    return tools


def _build_custom_tools(action: "InterviewAction") -> List[Tool]:
    tools: List[Tool] = []
    seen: set = set()

    for spec in action._registry.specs.values():
        for tdef in spec.skill_tools:
            full_name = skill_tool_name(spec, tdef.name)
            if full_name in seen:
                continue
            seen.add(full_name)
            tools.append(_make_custom_py_tool(action, tdef, spec))

    return tools


def _make_custom_py_tool(
    action: "InterviewAction", tdef: SkillToolDef, spec: InterviewSpec
) -> Tool:
    async def _handler(**kwargs) -> str:
        return await action._handle_custom_tool(tdef, spec, **kwargs)

    params_schema = (
        _build_json_schema_from_params(tdef.parameters)
        if tdef.parameters
        else {"type": "object", "properties": {}}
    )
    return Tool(
        name=skill_tool_name(spec, tdef.name),
        description=tdef.description or f"Custom interview tool: {tdef.name}",
        parameters_schema=params_schema,
        execute=_handler,
    )
