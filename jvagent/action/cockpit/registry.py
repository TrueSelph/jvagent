"""Cockpit tool registry: assembles harness + action + skill tools."""

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.action_resolver import ActionResolver
from jvagent.action.cockpit.artifact_tools import _build_artifact_tools
from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.conversation_tools import _build_conversation_tools
from jvagent.action.cockpit.memory_tools import _build_memory_tools
from jvagent.action.cockpit.response_tools import _build_response_tools
from jvagent.action.cockpit.search_tools import (
    KIND_SKILLS,
    KIND_TOOLS,
    _build_search_tools,
)
from jvagent.action.cockpit.skill_catalog import SkillCatalog
from jvagent.action.cockpit.skill_tools import _build_skill_tools
from jvagent.action.cockpit.task_tools import _build_task_tools
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


async def assemble_cockpit_tools(ctx: CockpitContext) -> ToolRegistry:
    """Assemble the full tool set for a cockpit run.

    Merges harness service tools, action tools (via ``Action.get_tools()``),
    and skill directory tools into a single ``ToolRegistry``.
    """
    registry = ToolRegistry()

    _register_harness_tools(registry, ctx)
    await _register_action_tools(registry, ctx)
    await _register_skill_tools(registry, ctx)

    logger.info(
        "CockpitToolRegistry: %d tools registered: %s",
        len(registry),
        registry.names(),
    )
    return registry


def _register_harness_tools(registry: ToolRegistry, ctx: CockpitContext) -> None:
    """Register memory, response, task, conversation, skill, artifact, and search harness tools.

    Artifact + cockpit_search tools are gated by config flags (enable_artifact_tools,
    enable_cockpit_search). The cockpit_search tool advertised at engine-time is
    restricted to skills + tools (no interact_actions).
    """
    cfg = ctx.config

    for tool in _build_memory_tools(ctx):
        registry.register(tool, prefix="harness")
    for tool in _build_response_tools(ctx):
        registry.register(tool, prefix="harness")
    for tool in _build_task_tools(ctx):
        registry.register(tool, prefix="harness")
    for tool in _build_conversation_tools(ctx):
        registry.register(tool, prefix="harness")
    for tool in _build_skill_tools(ctx):
        registry.register(tool, prefix="harness")

    if getattr(cfg, "enable_artifact_tools", True):
        for tool in _build_artifact_tools(ctx):
            registry.register(tool, prefix="harness")

    if getattr(cfg, "enable_cockpit_search", True):
        # Engine-context surface: skills + tools only (no interact_actions).
        for tool in _build_search_tools(ctx, permitted_kinds={KIND_SKILLS, KIND_TOOLS}):
            registry.register(tool, prefix="harness")


async def _register_action_tools(registry: ToolRegistry, ctx: CockpitContext) -> None:
    """Collect tools from all enabled actions via ``Action.get_tools()``."""
    if not ctx.agent:
        return

    try:
        actions_mgr = await ctx.agent.get_actions_manager()
        if not actions_mgr:
            return

        all_tools = await actions_mgr.get_all_tools()
        for tool in all_tools:
            registry.register(tool, prefix="action")
    except Exception as exc:
        logger.warning(
            "CockpitToolRegistry: failed to register action tools: %s",
            exc,
            exc_info=True,
        )


async def _register_skill_tools(registry: ToolRegistry, ctx: CockpitContext) -> None:
    """Load and register tool modules from skill bundle directories."""

    skill_state = getattr(ctx.visitor, "_skill_state", None) or {}
    # Use the shared catalog (deduplicated — single source of truth)
    catalog = skill_state.get("skill_catalog")
    discovered = skill_state.get("discovered_skills") or {}

    if not discovered:
        return

    action_resolver = None
    if ctx.agent:
        action_resolver = ActionResolver(ctx.agent)

    preloaded = ctx.preloaded_skills if hasattr(ctx, "preloaded_skills") else []

    if catalog is None and discovered:
        catalog = SkillCatalog(discovered)
        skill_state["skill_catalog"] = catalog

    for skill_name in preloaded:
        if skill_name not in discovered:
            continue
        await _load_one_skill(
            registry, skill_name, discovered[skill_name], catalog, action_resolver, ctx
        )


async def _load_one_skill(
    registry: ToolRegistry,
    skill_name: str,
    skill_data: Dict[str, Any],
    catalog: SkillCatalog,
    action_resolver: Optional[ActionResolver],
    ctx: CockpitContext,
) -> None:
    """Dynamically load tool modules from one skill bundle directory."""
    dir_path = skill_data.get("dir", "")
    tool_files = skill_data.get("tool_files", []) or []
    allowed_tools = set(skill_data.get("allowed_tools", []) or [])

    if not dir_path or not tool_files or not os.path.isdir(dir_path):
        return

    safe_name = skill_name.replace("-", "_")

    for file_path_str in tool_files:
        file_path = Path(file_path_str)
        if not file_path.is_file():
            continue
        if file_path.name.startswith("_"):
            continue

        try:
            tool = _load_tool_module(file_path, safe_name, allowed_tools, ctx)
            if tool is not None:
                registry.register(tool, prefix=safe_name)
        except Exception as exc:
            logger.warning(
                "CockpitToolRegistry: failed to load tool '%s' from skill '%s': %s",
                file_path.name,
                skill_name,
                exc,
            )


def _load_tool_module(
    file_path: Any,
    prefix: str,
    allowed_tools: set,
    ctx: CockpitContext,
) -> Optional[Tool]:
    """Import a single .py tool file and extract a ``Tool`` from it."""
    name = file_path.stem
    mod_name = f"jvagent_cockpit_skill_{prefix}_{name}"

    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    if not spec or not spec.loader:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    tool_def_fn = getattr(module, "get_tool_definition", None)
    execute_fn = getattr(module, "execute", None)

    raw_tool_name = None

    if tool_def_fn is not None and execute_fn is not None:
        try:
            tool_def_dict = tool_def_fn()
            if isinstance(tool_def_dict, dict):
                fn_block = tool_def_dict.get("function", {})
                raw_tool_name = fn_block.get("name") or tool_def_dict.get("name")
                description = fn_block.get("description") or tool_def_dict.get(
                    "description", ""
                )
                parameters = fn_block.get("parameters") or tool_def_dict.get(
                    "parameters", {"type": "object", "properties": {}}
                )
            else:
                return None
        except Exception:
            return None
    else:
        return None

    if not raw_tool_name:
        return None

    if allowed_tools and raw_tool_name not in allowed_tools:
        return None

    qualified_name = f"{prefix}__{raw_tool_name}"

    async def _wrapped_execute(**kwargs: Any) -> str:
        result = execute_fn(kwargs)
        import inspect

        if inspect.isawaitable(result):
            result = await result
        return str(result) if not isinstance(result, str) else result

    return Tool(
        name=qualified_name,
        description=str(description or ""),
        parameters_schema=(
            parameters
            if isinstance(parameters, dict)
            else {"type": "object", "properties": {}}
        ),
        execute=_wrapped_execute,
    )
