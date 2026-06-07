"""Tool builder for InterviewAction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..interview_action import InterviewAction

from jvagent.tooling.tool import Tool

from .interview_loader import InterviewSpec, ToolDef
from .session import InterviewSession, InterviewStatus, load_session

logger = logging.getLogger(__name__)

_TASK_OWNER_ACTION = "InterviewAction"


def _task_interview_type(handle: Any) -> Optional[str]:
    task_data = getattr(handle, "data", None) or {}
    if isinstance(task_data, dict):
        raw = task_data.get("interview_type")
        return str(raw) if raw else None
    return None


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

    async def _set_field(
        field: str = "", value: str = "", visitor: Any = None, name: str = "", **kwargs
    ) -> str:
        return await action._handle_set_field(
            field=field, value=value, visitor=visitor, name=name, **kwargs
        )

    tools.append(
        Tool(
            name="interview__set_field",
            description=(
                "Store a field value for the active interview question. Validation from "
                "interview spec validation runs automatically inside this tool — never call validator "
                "functions directly. The user's latest message is validated programmatically; "
                "ok:false / validation_failed means the value was NOT stored. "
                "On ok:true, saves and runs post_tools."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "Interview question name from skill frontmatter (e.g. available_times).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Deprecated alias for field.",
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "Optional hint; the user's latest utterance is validated when present."
                        ),
                    },
                },
                "required": ["field"],
            },
            execute=_set_field,
        )
    )

    async def _get_field(field: str, visitor: Any = None) -> str:
        return await action._handle_get_field(field, visitor)

    tools.append(
        Tool(
            name="interview__get_field",
            description="Retrieve the current value of a field in the active interview session.",
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
                "Mark an optional field as skipped. Returns next_tool to continue the interview."
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
                "Get the next question. Runs pre_tools for context. Returns next_questions "
                "and response_directive."
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
            description="Full status of the active interview session.",
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
            description="Cancel the active interview session.",
            parameters_schema={"type": "object", "properties": {}},
            execute=_cancel,
        )
    )

    return tools


def _build_custom_tools(action: "InterviewAction") -> List[Tool]:
    import importlib.util
    import os

    from ..runtime.hooks import load_hook_function
    from .decorators import interview_tool as _it

    tools: List[Tool] = []
    seen: set = set()

    for spec in action._registry.specs.values():
        for tdef in spec.tools:
            full_name = skill_tool_name(spec, tdef.name)
            if full_name in seen:
                continue
            seen.add(full_name)
            tools.append(_make_custom_py_tool(action, tdef, spec))

        custom_tools_path = os.path.join(spec.source_dir, "scripts", "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            custom_tools_path = os.path.join(spec.source_dir, "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            continue
        try:
            mod_name = f"interview_custom_tools_{spec.name}"
            loader_spec = importlib.util.spec_from_file_location(
                mod_name, custom_tools_path
            )
            if not loader_spec or not loader_spec.loader:
                continue
            module = importlib.util.module_from_spec(loader_spec)
            module.__dict__["interview_tool"] = _it
            loader_spec.loader.exec_module(module)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and getattr(attr, "_interview_tool", False):
                    tool_name = getattr(attr, "_tool_name", attr_name)
                    full_name = skill_tool_name(spec, tool_name)
                    if full_name in seen:
                        continue
                    seen.add(full_name)
                    tools.append(
                        Tool(
                            name=full_name,
                            description=getattr(
                                attr, "_tool_description", attr.__doc__ or ""
                            ),
                            parameters_schema=getattr(
                                attr,
                                "_tool_parameters_schema",
                                {"type": "object", "properties": {}},
                            ),
                            execute=_make_decorated_handler(action, attr, spec),
                        )
                    )
        except Exception as e:
            logger.error(
                "Failed to load custom_tools from %s: %s", custom_tools_path, e
            )

    return tools


def _make_decorated_handler(action: "InterviewAction", func, spec: InterviewSpec):
    async def _handler(**kwargs):
        return await action._handle_decorated_function(func, spec, **kwargs)

    return _handler


def _make_custom_py_tool(
    action: "InterviewAction", tdef: ToolDef, spec: InterviewSpec
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
