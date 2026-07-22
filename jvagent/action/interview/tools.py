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
        fields: Dict[str, str] | None = None,
        for_each_staged: Dict[str, Dict[str, str]] | None = None,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        return await action._handle_set_fields(
            fields=fields, for_each_staged=for_each_staged, visitor=visitor, **kwargs
        )

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

    async def _field_unavailable(
        field_key: str | None = None,
        field: str | None = None,
        reason: str | None = None,
        visitor: Any = None,
        **_: Any,
    ) -> str:
        key = field_key or field or ""
        return await action._handle_field_unavailable(key, visitor, reason or "")

    async def _next_field(visitor: Any = None) -> str:
        return await action._handle_next_field(visitor)

    async def _get_status(visitor: Any = None, **_: Any) -> str:
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
                "Validate and store interview field values. Requires an active "
                "interview session — open it with use_skill on the matching skill "
                'first. Args: {"fields": {"field_key": "value", ...}} — keys from '
                "field_reference[].key, never nested at the top level. Submit every "
                "confident value from the latest message in one call. Returns per-field "
                "results (stored / value / error); processing continues past failures, "
                "ok:false if any field was not stored."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": (
                            "Required. Map of interview field key to value. Example: "
                            '{"field_a": "Jane Doe", "field_b": "Monday at 9"}.'
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "for_each_staged": {
                        "type": "object",
                        "description": (
                            "For-each subpart data for items. During active iteration, "
                            "keys are 1-based indices matching for_each.index from the "
                            "response; values are maps of child field key to value for "
                            "that item. Save whatever the user gave for non-current "
                            "items immediately — even if the current item is still "
                            "incomplete and even if only some child fields were given "
                            "(partial maps are OK). During review, keys are 1-based "
                            "item indices targeting completed records for per-item "
                            "correction. Only used when a for_each expansion exists. "
                            'Example (full): {"2": {"child_a": "value", '
                            '"child_b": "other"}}. Example (partial): '
                            '{"2": {"child_a": "value"}}'
                        ),
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
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
                '{"field_key": "field_name"} — optional; if omitted, the current '
                "pending field is skipped. Then follow the returned "
                "response_directive for the next step — it routes to "
                "interview__next_field while fields remain, or interview__review "
                "once the last field is collected or skipped."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field_key": {
                        "type": "string",
                        "description": (
                            'Interview field key to skip (e.g. "phone_number"). '
                            "Optional — defaults to the current pending field."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            execute=_skip_field,
        ),
        Tool(
            name="interview__field_unavailable",
            description=(
                "Call when the user says they CANNOT supply the pending field right "
                'now ("I don\'t have the tracking number", "I\'ll have to check", '
                '"can\'t find it") — distinct from declining an optional field '
                "(use interview__skip_field) or cancelling the whole request (use "
                'interview__cancel). Args: {"field_key": "field_name"} — optional; '
                'defaults to the current pending field; optional "reason" carries '
                "the user's words. The server applies the field's configured policy "
                "(park / cancel / relax) and returns a response_directive to relay — "
                "usually the request is set aside and resumes when the user returns "
                "with the value."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field_key": {
                        "type": "string",
                        "description": (
                            "Interview field key the user cannot supply. Optional — "
                            "defaults to the current pending field."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Optional short paraphrase of why they can't supply it."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            execute=_field_unavailable,
        ),
        Tool(
            name="interview__next_field",
            description=(
                "Requires an active interview session (open it with use_skill on the "
                "matching skill first). Get the next field to present. Runs "
                "pre_processor hooks for context. Returns next_field "
                "{key, prompt, required}, skipped_fields, and response_directive. "
                "Optional fields may be skipped via interview__skip_field."
            ),
            parameters_schema=no_args,
            execute=_next_field,
        ),
        Tool(
            name="interview__get_status",
            description=(
                "Read surface for the active interview: collected fields, "
                "skipped_fields, next_field_key, confirm mode, status, and the full "
                "field_reference catalog (key/prompt/guidance/required for every "
                "field). Use to re-pull field_reference when earlier context thins. "
                "No arguments."
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
