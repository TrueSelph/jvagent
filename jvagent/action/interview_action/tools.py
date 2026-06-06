"""Tool builder for InterviewAction.

Generates the full tool surface from a contract registry:
- 8 fixed data-operation tools (set_field, get_field, skip_field, next_question, get_status, review, complete, cancel)
- N custom tools (one per ToolDef in the contract, loaded from scripts/custom_tools.py)

Field validation runs inside interview__set_field using per-question validators
from contract.yaml (builtin or custom_tools.py functions).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .interview_action import InterviewAction

from jvagent.tooling.tool import Tool

from .contract_loader import (
    CompletionDef,
    InterviewContract,
    ReviewDef,
    ToolDef,
)
from .session import InterviewSession, InterviewStatus, load_session

logger = logging.getLogger(__name__)

_TASK_OWNER_ACTION = "InterviewAction"


def _task_interview_type(handle: Any) -> Optional[str]:
    task_data = getattr(handle, "data", None) or {}
    if isinstance(task_data, dict):
        raw = task_data.get("interview_type")
        return str(raw) if raw else None
    return None


def _resolve_init_interview_type(
    action: "InterviewAction",
    interview_type: str,
    visitor: Any,
    kwargs: Dict[str, Any],
) -> str:
    """Resolve interview_type from args or a single active skill/interview task."""
    resolved = (interview_type or kwargs.get("interview_type") or "").strip()
    if resolved:
        return resolved
    if visitor is None:
        return ""
    try:
        store = visitor.tasks
    except Exception:
        return ""

    known = set(action._contract_registry.list_contracts())
    candidates: List[str] = []
    for name in action._contract_registry.list_contracts():
        try:
            if store.list(status="active", owner_action=name):
                candidates.append(name)
        except Exception:
            pass
    try:
        for handle in (
            store.list(status="active", owner_action=_TASK_OWNER_ACTION) or []
        ):
            it = _task_interview_type(handle)
            if it and it in known:
                candidates.append(it)
    except Exception:
        pass

    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    return ""


def skill_tool_name(contract: InterviewContract, tool_name: str) -> str:
    """Registered name for a skill-specific custom tool."""
    return f"{contract.name}__{tool_name}"


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
    """Build the complete tool surface for the action."""
    tools: List[Tool] = []

    tools.extend(_build_data_tools(action))
    tools.extend(_build_custom_tools(action))

    return tools


def _build_data_tools(action: "InterviewAction") -> List[Tool]:
    """Build the fixed data-operation tools (session starts via use_skill)."""
    tools: List[Tool] = []

    async def _set_field(
        field: str = "",
        value: str = "",
        visitor: Any = None,
        name: str = "",
        **kwargs,
    ) -> str:
        return await action._handle_set_field(
            field=field, value=value, visitor=visitor, name=name, **kwargs
        )

    tools.append(
        Tool(
            name="interview__set_field",
            description=(
                "Store a field value. Validates using the question's contract validator "
                "before saving. Use parameter field (contract field name, e.g. tracking_number, "
                "description, email) — not name. Returns ok:false on validation_failed (post_tools "
                "do not run). On ok:true, saves the field and runs post_tools when configured. "
                "Read post_tools_results and decide next step — call interview__next_question to advance."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": (
                            "Contract field name (e.g. tracking_number, description, email). "
                            "Required — do not use name."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": "Deprecated alias for field — use field instead.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The user's answer to store (validated automatically)",
                    },
                },
                "required": ["value"],
            },
            execute=_set_field,
        )
    )

    async def _get_field(field: str, visitor: Any = None) -> str:
        return await action._handle_get_field(field, visitor)

    tools.append(
        Tool(
            name="interview__get_field",
            description=(
                "Retrieve the current value of a field in the active interview session. "
                "Returns the value or null if the field has not been set."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "The field name to retrieve",
                    }
                },
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
                "Mark an optional field as skipped. Use when the user declines or cannot provide "
                "a value for an optional field (interpret their intent from the conversation). "
                "Returns ok:true with updated fields. Call interview__next_question to get what to ask next."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "The optional field name to skip (e.g. 'id_card', 'email')",
                    }
                },
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
                "Get the next question to ask. Runs pre_tools for context (e.g. suggested "
                "phone number on WhatsApp). Call after set_field when ok:true and post_tools "
                "allow continuing, after skip_field, or after init (unless skip_to_review). "
                "Returns ok, next_questions, pre_tools_results, and response_directive."
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
                "Get the full status of the active interview session including all collected "
                "field values, skipped fields, missing required fields, and available contract "
                "questions. Use this to understand what data has been collected and what "
                "still needs to be asked."
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
            description=(
                "Get a formatted review summary of all collected data. Call this when all "
                "required fields have been gathered and you are ready to present the data "
                "to the user for confirmation before completing the interview."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_review,
        )
    )

    async def _complete(visitor: Any = None) -> str:
        return await action._handle_complete(visitor)

    tools.append(
        Tool(
            name="interview__complete",
            description=(
                "Complete the interview. If a completion function is configured in the contract, "
                "it will be called (e.g. to create an account). Only call this after the user "
                "has confirmed all data during the review step."
            ),
            parameters_schema={"type": "object", "properties": {}},
            execute=_complete,
        )
    )

    async def _cancel(visitor: Any = None) -> str:
        return await action._handle_cancel(visitor)

    tools.append(
        Tool(
            name="interview__cancel",
            description="Cancel the active interview session. Clears all collected data.",
            parameters_schema={"type": "object", "properties": {}},
            execute=_cancel,
        )
    )

    return tools


def _build_custom_tools(action: "InterviewAction") -> List[Tool]:
    """Build one tool per ToolDef across all loaded contracts.

    Custom tools are either:
    - action/method references (call another action's method)
    - plain Python functions in scripts/custom_tools.py (via contract ``function:``)
    - legacy @interview_tool decorated functions not listed in contract.tools
    """
    tools: List[Tool] = []
    seen: set = set()

    for contract in action._contract_registry._contracts.values():
        for tdef in contract.tools:
            full_name = skill_tool_name(contract, tdef.name)
            if full_name in seen:
                continue
            seen.add(full_name)
            if tdef.function:
                tools.append(_make_custom_py_tool(action, tdef, contract))
            else:
                tools.append(_make_custom_action_tool(action, tdef, contract))

    tools.extend(_discover_decorated_tools(action, seen))

    return tools


def _make_custom_py_tool(
    action: "InterviewAction",
    tdef: ToolDef,
    contract: InterviewContract,
) -> Tool:
    """Create a tool from a Python function defined in the contract's scripts/custom_tools.py."""

    async def _handler(**kwargs) -> str:
        return await action._handle_custom_tool(tdef, contract, **kwargs)

    params_schema = (
        _build_json_schema_from_params(tdef.parameters)
        if tdef.parameters
        else {
            "type": "object",
            "properties": {},
        }
    )

    return Tool(
        name=skill_tool_name(contract, tdef.name),
        description=tdef.description or f"Custom interview tool: {tdef.name}",
        parameters_schema=params_schema,
        execute=_handler,
    )


def _make_custom_action_tool(
    action: "InterviewAction",
    tdef: ToolDef,
    contract: InterviewContract,
) -> Tool:
    """Create a tool that invokes another action's method (action.method reference)."""

    async def _handler(**kwargs) -> str:
        return await action._handle_action_tool(tdef, contract, **kwargs)

    params_schema = (
        _build_json_schema_from_params(tdef.parameters)
        if tdef.parameters
        else {
            "type": "object",
            "properties": {},
        }
    )

    return Tool(
        name=skill_tool_name(contract, tdef.name),
        description=tdef.description or f"Action-backed tool: {tdef.name}",
        parameters_schema=params_schema,
        execute=_handler,
    )


def _discover_decorated_tools(
    action: "InterviewAction",
    seen: Optional[set] = None,
) -> List[Tool]:
    """Discover @interview_tool decorated functions from scripts/custom_tools.py."""
    from .decorators import interview_tool as _it

    tools: List[Tool] = []
    if seen is None:
        seen = set()

    for contract in action._contract_registry._contracts.values():
        custom_tools_path = os.path.join(
            contract.source_dir, "scripts", "custom_tools.py"
        )
        if not os.path.isfile(custom_tools_path):
            custom_tools_path = os.path.join(contract.source_dir, "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"interview_custom_tools_{contract.name}", custom_tools_path
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            module.__dict__["interview_tool"] = _it
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and getattr(attr, "_interview_tool", False):
                    tool_name = getattr(attr, "_tool_name", attr_name)
                    full_name = skill_tool_name(contract, tool_name)
                    if full_name in seen:
                        continue
                    seen.add(full_name)

                    tool_desc = getattr(attr, "_tool_description", attr.__doc__ or "")
                    tool_params = getattr(
                        attr,
                        "_tool_parameters_schema",
                        {"type": "object", "properties": {}},
                    )

                    async def _make_handler(
                        func=attr, _action=action, _contract=contract, **kwargs
                    ):
                        return await _action._handle_decorated_function(
                            func, _contract, **kwargs
                        )

                    tools.append(
                        Tool(
                            name=full_name,
                            description=tool_desc,
                            parameters_schema=tool_params,
                            execute=_make_handler,
                        )
                    )
        except Exception as e:
            logger.error(
                f"Failed to load custom_tools.py from {custom_tools_path}: {e}"
            )

    return tools
