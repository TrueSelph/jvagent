"""Tool definitions for LeadGenAction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvagent.tooling.tool import Tool

from .spec import LeadGenSpec, SkillToolDef

if TYPE_CHECKING:
    from .leadgen_action import LeadGenAction


def skill_tool_name(spec: LeadGenSpec, tool_name: str) -> str:
    return f"{spec.name}__{tool_name}"


def build_tools(action: "LeadGenAction") -> List[Tool]:
    return _build_core_tools(action) + _build_custom_tools(action)


def _field_properties(action: "LeadGenAction") -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "fields": {
            "type": "object",
            "description": "Flat key-value map of lead fields (snake_case keys).",
            "additionalProperties": {"type": "string"},
        },
        "skill": {
            "type": "string",
            "description": "Optional leadgen skill name when multiple skills are registered.",
        },
    }
    for spec in action._registry.specs.values():
        for f in spec.fields:
            if f.key not in props:
                props[f.key] = {
                    "type": "string",
                    "description": f"Lead field: {f.key}",
                }
    for key, cfg in (action.default_fields or {}).items():
        if key not in props:
            desc = cfg.get("description", "") if isinstance(cfg, dict) else ""
            props[key] = {"type": "string", "description": desc or f"Lead field: {key}"}
    return props


def _build_core_tools(action: "LeadGenAction") -> List[Tool]:
    props = _field_properties(action)
    optional_skill = {
        "type": "object",
        "properties": {
            "skill": props.pop("skill", {"type": "string"}),
        },
    }

    async def _capture(
        fields: Optional[Dict[str, str]] = None,
        skill: Optional[str] = None,
        visitor: Any = None,
        **kwargs: Any,
    ) -> str:
        return await action._handle_capture(
            fields=fields,
            skill=skill,
            visitor=visitor,
            **kwargs,
        )

    async def _retrieve(
        skill: Optional[str] = None, visitor: Any = None, **_: Any
    ) -> str:
        return await action._handle_retrieve(skill=skill, visitor=visitor)

    async def _status(
        skill: Optional[str] = None, visitor: Any = None, **_: Any
    ) -> str:
        return await action._handle_status(skill=skill, visitor=visitor)

    async def _sync(skill: Optional[str] = None, visitor: Any = None, **_: Any) -> str:
        return await action._handle_sync(skill=skill, visitor=visitor)

    capture_props = dict(props)
    capture_props["skill"] = {
        "type": "string",
        "description": "Optional leadgen skill name.",
    }

    return [
        Tool(
            name="leadgen__capture",
            description=(
                "Save or update lead fields from the latest message. Send every new value in "
                "one call; map a refusal to the field's decline text (short refusals are "
                "normalised; never send the literal token decline_value). Returns status, fields, "
                "missing_fields, next_required, optional_fields, field_reference, "
                "validation_errors, sync_result, and a directive. Follow the directive; a "
                "capture is not a close."
            ),
            parameters_schema={
                "type": "object",
                "properties": capture_props,
                "minProperties": 1,
            },
            execute=_capture,
        ),
        Tool(
            name="leadgen__retrieve",
            description=(
                "Load the current lead profile, next_required, optional_fields, field_reference, "
                "and the ask directive. Use mid-conversation, once "
                "a request cycle is already underway, when you need profile context and "
                "the visitor supplied no new lead data. NEVER the first call on a "
                "post-sync turn: when the skill provides a prepare tool, it runs first — "
                'even for a bare greeting like "hey" with no product named. Do not use '
                "retrieve to restore the profile after a sync or to open a turn; it reads "
                "the cleared record and re-asks for details the visitor already gave."
            ),
            parameters_schema=optional_skill,
            execute=_retrieve,
        ),
        Tool(
            name="leadgen__status",
            description=(
                "Lightweight progress: missing/required fields, score, sync digest. "
                "No full field dump."
            ),
            parameters_schema=optional_skill,
            execute=_status,
        ),
        Tool(
            name="leadgen__sync",
            description=(
                "Explicitly push the lead profile to external MCP destinations. "
                "Only needed when sync.mode is manual or for debugging."
            ),
            parameters_schema=optional_skill,
            execute=_sync,
        ),
    ]


def _build_custom_tools(action: "LeadGenAction") -> List[Tool]:
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
    action: "LeadGenAction", tdef: SkillToolDef, spec: LeadGenSpec
) -> Tool:
    async def _handler(**kwargs: Any) -> str:
        return await action._handle_custom_tool(tdef, spec, **kwargs)

    schema: Dict[str, Any] = {"type": "object", "properties": {}}
    if tdef.parameters:
        schema = {"type": "object", "properties": {}, "required": []}
        for pname, pdef in tdef.parameters.items():
            if isinstance(pdef, dict):
                schema["properties"][pname] = dict(pdef)
            else:
                schema["properties"][pname] = {
                    "type": "string",
                    "description": str(pdef),
                }

    return Tool(
        name=skill_tool_name(spec, tdef.name),
        description=tdef.description or f"Custom leadgen tool: {tdef.name}",
        parameters_schema=schema,
        execute=_handler,
    )
