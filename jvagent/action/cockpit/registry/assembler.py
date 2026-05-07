"""Cockpit tool registry: assembles harness + action + skill tools."""

import importlib.util
import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.catalog.action_resolver import ActionResolver
from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.tools.artifact import _build_artifact_tools
from jvagent.action.cockpit.tools.clock import _build_clock_tools
from jvagent.action.cockpit.tools.conversation import _build_conversation_tools
from jvagent.action.cockpit.tools.identity import _build_identity_tools
from jvagent.action.cockpit.tools.memory import _build_memory_tools
from jvagent.action.cockpit.tools.response import _build_response_tools
from jvagent.action.cockpit.tools.search import (
    KIND_SKILLS,
    KIND_TOOLS,
    _build_search_tools,
)
from jvagent.action.cockpit.tools.skill import _build_skill_tools
from jvagent.action.cockpit.tools.task import _build_task_tools
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# Skill state key used to expose the skill load report for observability.
SKILL_LOAD_REPORT_KEY = "cockpit_skill_load_report"


@dataclass
class SkillLoadEntry:
    """Per-tool-file load outcome for one skill bundle."""

    skill_name: str
    file: str
    status: str  # "loaded" | "skipped" | "failed"
    tool_name: Optional[str] = None
    reason: Optional[str] = None  # populated for skipped/failed


@dataclass
class SkillLoadReport:
    """Aggregated outcome of a cockpit skill loading pass.

    Persisted on ``visitor._skill_state[SKILL_LOAD_REPORT_KEY]`` so smoke
    harness, debug endpoints, and tests can inspect which tools loaded and
    why others did not. The same report shape is also logged at INFO level.
    """

    entries: List[SkillLoadEntry] = field(default_factory=list)

    def loaded(self) -> List[SkillLoadEntry]:
        return [e for e in self.entries if e.status == "loaded"]

    def skipped(self) -> List[SkillLoadEntry]:
        return [e for e in self.entries if e.status == "skipped"]

    def failed(self) -> List[SkillLoadEntry]:
        return [e for e in self.entries if e.status == "failed"]

    def summary_line(self) -> str:
        return (
            f"loaded={len(self.loaded())} "
            f"skipped={len(self.skipped())} "
            f"failed={len(self.failed())}"
        )


async def assemble_cockpit_tools(ctx: CockpitContext) -> ToolRegistry:
    """Assemble the full tool set for a cockpit run.

    Merges harness service tools, action tools (via ``Action.get_tools()``),
    and skill directory tools into a single ``ToolRegistry``. After the full
    surface is assembled, the registry is filtered against the agent's
    ``AccessControlAction`` (when present) so per-user policies apply
    uniformly across harness + action + skill tools.
    """
    from jvagent.action.cockpit.registry.access import filter_tool_registry_by_access

    registry = ToolRegistry()

    _register_harness_tools(registry, ctx)
    await _register_action_tools(registry, ctx)
    await _register_skill_tools(registry, ctx)

    user_id = getattr(ctx, "user_id", None)
    channel = getattr(ctx, "channel", "default") or "default"
    removed = await filter_tool_registry_by_access(
        registry, ctx.agent, user_id=user_id, channel=channel
    )
    if removed:
        logger.info(
            "CockpitToolRegistry: access control removed %d tool(s) for user=%s",
            removed,
            user_id,
        )

    logger.info(
        "CockpitToolRegistry: %d tools registered: %s",
        len(registry),
        registry.names(),
    )
    return registry


# Tool tier whitelists. The model's prompt grows with every additional tool
# schema, so most agents only need a curated subset. Tier values:
#
# - ``minimal``: bare essentials (~8 tools). Default for production cost focus.
# - ``standard``: common workflows (~17 tools). Sensible default for most agents.
# - ``full``: every harness tool (~23 tools). Use when in active development or
#   when an agent specifically needs the long tail.
#
# Action and skill tools are NOT filtered by tier — they are always registered
# in full (they reflect deliberate agent configuration).
_TIER_MINIMAL = {
    "memory_set",
    "memory_get",
    "response_publish",
    "task_create_plan",
    "task_update_step",
    "cockpit_search",
    "skill_search",
    "skill_read",
    # Identity + clock are always included — cheap, frequently needed, and
    # the model otherwise hallucinates the time or guesses at the user's name.
    "get_current_datetime",
    "get_user_name",
}

_TIER_STANDARD = _TIER_MINIMAL | {
    "memory_search",
    "memory_list",
    "memory_set_preference",
    "task_get_status",
    "task_add_step",
    "conversation_search",
    "artifact_add",
    "artifact_get",
    "artifact_search",
}

# ``full`` is realised by skipping the filter (no whitelist).
_VALID_TIERS = {"minimal", "standard", "full"}


def _resolve_tier_whitelist(tier: str) -> Optional[set]:
    """Return the whitelist for ``tier``, or ``None`` to mean 'no filter'."""
    if tier == "minimal":
        return set(_TIER_MINIMAL)
    if tier == "standard":
        return set(_TIER_STANDARD)
    if tier == "full":
        return None
    logger.warning(
        "CockpitToolRegistry: unknown tool_tier=%r, falling back to 'standard'",
        tier,
    )
    return set(_TIER_STANDARD)


def _register_harness_tools(registry: ToolRegistry, ctx: CockpitContext) -> None:
    """Register memory, response, task, conversation, skill, artifact, and search harness tools.

    Filtered by ``cfg.tool_tier`` (``minimal``/``standard``/``full``) so the
    model's prompt isn't bloated with tools the agent does not need. Artifact
    tools and ``cockpit_search`` remain gated by their own enable flags so
    operators can override the tier individually.
    """
    cfg = ctx.config
    tier = getattr(cfg, "tool_tier", "standard") or "standard"
    whitelist = _resolve_tier_whitelist(tier)

    def _register(tool: Tool) -> None:
        if whitelist is not None and tool.name not in whitelist:
            return
        registry.register(tool, prefix="harness")

    for tool in _build_memory_tools(ctx):
        _register(tool)
    for tool in _build_response_tools(ctx):
        _register(tool)
    for tool in _build_task_tools(ctx):
        _register(tool)
    for tool in _build_conversation_tools(ctx):
        _register(tool)
    for tool in _build_skill_tools(ctx):
        _register(tool)
    for tool in _build_clock_tools(ctx):
        _register(tool)
    for tool in _build_identity_tools(ctx):
        _register(tool)

    if getattr(cfg, "enable_artifact_tools", True):
        for tool in _build_artifact_tools(ctx):
            _register(tool)

    if getattr(cfg, "enable_cockpit_search", True):
        # Engine-context surface: skills + tools only (no interact_actions).
        for tool in _build_search_tools(ctx, permitted_kinds={KIND_SKILLS, KIND_TOOLS}):
            _register(tool)


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
    """Load and register tool modules from skill bundle directories.

    Records every load attempt in a ``SkillLoadReport`` published on
    ``ctx.visitor._skill_state[SKILL_LOAD_REPORT_KEY]`` for observability.
    """

    skill_state = getattr(ctx.visitor, "_skill_state", None)
    if skill_state is None:
        # Without a visitor state bag we still want to load tools, but the
        # report has nowhere to go — keep it local for log output only.
        skill_state = {}
    # Use the shared catalog (deduplicated — single source of truth)
    catalog = skill_state.get("skill_catalog")
    discovered = skill_state.get("discovered_skills") or {}

    report = SkillLoadReport()
    skill_state[SKILL_LOAD_REPORT_KEY] = report

    action_resolver = None
    if ctx.agent:
        action_resolver = ActionResolver(ctx.agent)
        # Attach to visitor so skill tools can find it via getattr(visitor, "action_resolver", None)
        try:
            setattr(ctx.visitor, "action_resolver", action_resolver)
        except Exception:
            pass

    preloaded = ctx.preloaded_skills if hasattr(ctx, "preloaded_skills") else []

    if catalog is None and discovered:
        catalog = SkillCatalog(discovered)
        skill_state["skill_catalog"] = catalog

    for skill_name in preloaded:
        if skill_name not in discovered:
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file="",
                    status="skipped",
                    reason="not in discovered_skills",
                )
            )
            continue
        await _load_one_skill(
            registry,
            skill_name,
            discovered[skill_name],
            catalog,
            action_resolver,
            ctx,
            report,
        )

    if report.entries:
        logger.info(
            "CockpitSkillLoad: skill=%s %s",
            preloaded,
            report.summary_line(),
        )
        for failed in report.failed():
            logger.warning(
                "CockpitSkillLoad: failed skill=%s file=%s reason=%s",
                failed.skill_name,
                failed.file,
                failed.reason,
            )


async def _load_one_skill(
    registry: ToolRegistry,
    skill_name: str,
    skill_data: Dict[str, Any],
    catalog: SkillCatalog,
    action_resolver: Optional[ActionResolver],
    ctx: CockpitContext,
    report: SkillLoadReport,
) -> None:
    """Dynamically load tool modules from one skill bundle directory.

    Each tool-file outcome is appended to ``report``. The skill name + the
    relative file name are kept on every entry so failures stay traceable.
    """
    dir_path = skill_data.get("dir", "")
    tool_files = skill_data.get("tool_files", []) or []
    allowed_tools = set(skill_data.get("allowed_tools", []) or [])

    if not dir_path or not tool_files or not os.path.isdir(dir_path):
        report.entries.append(
            SkillLoadEntry(
                skill_name=skill_name,
                file=dir_path or "",
                status="skipped",
                reason=(
                    "missing dir, tool_files, or directory not found"
                    if not (dir_path and tool_files)
                    else f"directory not found: {dir_path}"
                ),
            )
        )
        return

    safe_name = skill_name.replace("-", "_")

    for file_path_str in tool_files:
        file_path = Path(file_path_str)
        if not file_path.is_file():
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file=str(file_path),
                    status="skipped",
                    reason="file not found",
                )
            )
            continue
        if file_path.name.startswith("_"):
            # Private modules (e.g. _helpers.py) are intentionally skipped.
            continue

        try:
            tool, skip_reason = _load_tool_module(
                file_path, safe_name, allowed_tools, ctx
            )
        except Exception as exc:
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file=str(file_path),
                    status="failed",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        if tool is None:
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file=str(file_path),
                    status="skipped",
                    reason=skip_reason or "no tool produced",
                )
            )
            continue

        try:
            registered_name = registry.register(tool, prefix=safe_name)
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file=str(file_path),
                    status="loaded",
                    tool_name=registered_name,
                )
            )
        except Exception as exc:
            report.entries.append(
                SkillLoadEntry(
                    skill_name=skill_name,
                    file=str(file_path),
                    status="failed",
                    reason=f"register: {type(exc).__name__}: {exc}",
                )
            )


def _load_tool_module(
    file_path: Any,
    prefix: str,
    allowed_tools: set,
    ctx: CockpitContext,
) -> "tuple[Optional[Tool], Optional[str]]":
    """Import a single .py tool file and extract a ``Tool`` from it.

    Returns ``(tool, skip_reason)`` — exactly one is populated. ``tool`` is
    None when the file does not provide a usable cockpit tool (missing
    ``get_tool_definition`` / ``execute``, malformed schema, or filtered by
    ``allowed_tools``); ``skip_reason`` describes why for the load report.

    Module loading uses ``importlib.util`` and POPS ``sys.modules`` on any
    exec failure so a partially-initialised module never persists. Re-loads
    of the same skill replace any prior copy in ``sys.modules`` cleanly.
    """
    name = file_path.stem
    mod_name = f"jvagent_cockpit_skill_{prefix}_{name}"

    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    if not spec or not spec.loader:
        return None, "spec_from_file_location returned None"

    # Drop any stale module under this key from a prior load — re-loads must
    # see the current file contents, not a cached previous version.
    sys.modules.pop(mod_name, None)

    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Critical: do not leave a partially-initialised module in sys.modules.
        sys.modules.pop(mod_name, None)
        raise

    tool_def_fn = getattr(module, "get_tool_definition", None)
    execute_fn = getattr(module, "execute", None)

    if tool_def_fn is None or execute_fn is None:
        sys.modules.pop(mod_name, None)
        missing = []
        if tool_def_fn is None:
            missing.append("get_tool_definition")
        if execute_fn is None:
            missing.append("execute")
        return None, f"module missing: {', '.join(missing)}"

    try:
        tool_def_dict = tool_def_fn()
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        raise RuntimeError(f"get_tool_definition() raised: {exc}") from exc

    if not isinstance(tool_def_dict, dict):
        sys.modules.pop(mod_name, None)
        return None, "get_tool_definition() did not return a dict"

    fn_block = tool_def_dict.get("function", {})
    raw_tool_name = fn_block.get("name") or tool_def_dict.get("name")
    description = fn_block.get("description") or tool_def_dict.get("description", "")
    parameters = fn_block.get("parameters") or tool_def_dict.get(
        "parameters", {"type": "object", "properties": {}}
    )

    if not raw_tool_name:
        sys.modules.pop(mod_name, None)
        return None, "tool definition missing name"

    qualified_name = f"{prefix}__{raw_tool_name}"
    if allowed_tools and raw_tool_name not in allowed_tools and qualified_name not in allowed_tools:
        sys.modules.pop(mod_name, None)
        return None, f"name '{raw_tool_name}' not in allowed_tools (checked raw '{raw_tool_name}' and qualified '{qualified_name}')"

    async def _wrapped_execute(**kwargs: Any) -> str:
        sig = inspect.signature(execute_fn)
        if "visitor" in sig.parameters:
            result = execute_fn(kwargs, visitor=ctx.visitor)
        else:
            result = execute_fn(kwargs)

        if inspect.isawaitable(result):
            result = await result
        return str(result) if not isinstance(result, str) else result

    return (
        Tool(
            name=qualified_name,
            description=str(description or ""),
            parameters_schema=(
                parameters
                if isinstance(parameters, dict)
                else {"type": "object", "properties": {}}
            ),
            execute=_wrapped_execute,
        ),
        None,
    )
