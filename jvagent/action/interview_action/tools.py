"""Tool definitions for InterviewAction — fixed interview__* tools plus per-skill custom tools."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from jvagent.tooling.tool import Tool

from .spec import InterviewSpec, SkillToolDef

if TYPE_CHECKING:
    from .interview_action import InterviewAction

logger = logging.getLogger(__name__)


def skill_tool_name(spec: InterviewSpec, tool_name: str) -> str:
    return f"{spec.name}__{tool_name}"


def build_tools(action: "InterviewAction") -> List[Tool]:
    return _build_core_tools(action) + _build_custom_tools(action)


def _build_core_tools(action: "InterviewAction") -> List[Tool]:
    no_args = {"type": "object", "properties": {}}

    async def _set_fields(
        fields: Dict[str, str] | None = None, visitor: Any = None, **kwargs: Any
    ) -> str:
        return await action._handle_set_fields(fields=fields, visitor=visitor, **kwargs)

    async def _skip_field(
        field_key: str | None = None,
        field: str | None = None,
        visitor: Any = None,
        **_: Any,
    ) -> str:
        key = field_key or field
        if not key:
            return await action._handle_skip_field("", visitor)
        return await action._handle_skip_field(key, visitor)

    async def _next_field(visitor: Any = None) -> str:
        return await action._handle_next_field(visitor)

    async def _get_status(visitor: Any = None) -> str:
        return await action._handle_get_status(visitor)

    async def _review(visitor: Any = None) -> str:
        return await action._handle_review(visitor)

    async def _complete(visitor: Any = None) -> str:
        return await action._handle_complete(visitor)

    async def _cancel(visitor: Any = None) -> str:
        return await action._handle_cancel(visitor)

    async def _reset(visitor: Any = None) -> str:
        return await action._handle_reset(visitor)

    return [
        Tool(
            name="interview__set_fields",
            description=(
                "Requires an active interview session (open with use_skill on the "
                "matching skill first). Validate and store one or more interview "
                "field values. Use for new answers, corrections to previously stored "
                "fields, or batch extraction from the user's latest message. "
                'Args shape is always {"fields": {"field_key": "value", ...}} — '
                "never pass field keys at the top level of args. "
                "Use keys from awaiting_fields[].key in the latest activation, "
                "next_field, get_status, or set_fields observation — never invent keys. "
                "Validators run per field inside this tool — never call validator "
                "functions directly. ok:false means a field was not stored. On ok:true, "
                "runs post_processor hooks when configured. Chain interview__next_field "
                "or interview__review per the SKILL procedure."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": (
                            "Required. Map of interview field key to value. Example: "
                            '{"user_name": "Jane Doe", "available_times": "Monday at 9"}.'
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["fields"],
                "additionalProperties": False,
            },
            execute=_set_fields,
        ),
        Tool(
            name="interview__skip_field",
            description=(
                "Mark an optional field as skipped. Args: "
                '{"field_key": "field_name"}. Follow response_directive; '
                "call interview__next_field when the response chains mechanically."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field_key": {
                        "type": "string",
                        "description": (
                            "Required. Interview field key to skip "
                            '(e.g. "phone_number").'
                        ),
                    },
                },
                "required": ["field_key"],
                "additionalProperties": False,
            },
            execute=_skip_field,
        ),
        Tool(
            name="interview__next_field",
            description=(
                "Requires an active interview session (open with use_skill on the "
                "matching skill first). Get the next field to present. Runs "
                "pre_processor hooks for context. Returns next_field, "
                "missing_required, and response_directive."
            ),
            parameters_schema=no_args,
            execute=_next_field,
        ),
        Tool(
            name="interview__get_status",
            description=(
                "Read surface for the active interview: fields, missing_required, "
                "skipped_fields, confirm, and session status."
            ),
            parameters_schema=no_args,
            execute=_get_status,
        ),
        Tool(
            name="interview__review",
            description="Formatted review summary before completion.",
            parameters_schema=no_args,
            execute=_review,
        ),
        Tool(
            name="interview__complete",
            description="Complete the interview after user confirms review.",
            parameters_schema=no_args,
            execute=_complete,
        ),
        Tool(
            name="interview__cancel",
            description=(
                "Cancel and close the active interview session. Use when the user "
                "wants to stop, quit, or cancel — not when they want to start over."
            ),
            parameters_schema=no_args,
            execute=_cancel,
        ),
        Tool(
            name="interview__reset",
            description=(
                "Clear progress and restart the active interview from the first "
                "field, or run the skill's custom reset handler when "
                "handlers.reset is declared. Use for start-over intent — "
                "not for cancel/stop/quit. Follow response_directive."
            ),
            parameters_schema=no_args,
            execute=_reset,
        ),
    ]


def _build_json_schema_from_params(params: Dict[str, Any]) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for pname, pdef in params.items():
        if isinstance(pdef, dict):
            prop = dict(pdef)
            prop.setdefault("type", "string")
            schema["properties"][pname] = prop
            if pdef.get("required", False):
                schema["required"].append(pname)
        else:
            schema["properties"][pname] = {"type": "string", "description": str(pdef)}
    if not schema["required"]:
        del schema["required"]
    return schema


def _build_custom_tools(action: "InterviewAction") -> List[Tool]:
    tools: List[Tool] = []
    seen: set = set()
    for spec in action._registry.specs.values():
        for tdef in spec.skill_tools:
            full_name = skill_tool_name(spec, tdef.name)
            if full_name in seen:
                continue
            seen.add(full_name)
            tools.append(_make_custom_tool(action, tdef, spec))
    return tools


def _make_custom_tool(
    action: "InterviewAction", tdef: SkillToolDef, spec: InterviewSpec
) -> Tool:
    async def _handler(**kwargs) -> str:
        return await action._handle_custom_tool(tdef, spec, **kwargs)

    return Tool(
        name=skill_tool_name(spec, tdef.name),
        description=tdef.description or f"Custom interview tool: {tdef.name}",
        parameters_schema=(
            _build_json_schema_from_params(tdef.parameters)
            if tdef.parameters
            else {"type": "object", "properties": {}}
        ),
        execute=_handler,
    )
