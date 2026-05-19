"""Cockpit tool registry: assembles harness + action + skill tools."""

import importlib.util
import inspect
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    # Expand preloaded list with declared ``coactivate-with`` companions.
    # Companions inherit activation from the seed skill so the engine has
    # the related tool surface (e.g. drill-down from an insights query into
    # a single entry) without a mid-loop ``skill_activate`` round trip.
    # Depth cap = 2 (seed → companion → companion-of-companion).
    if catalog is not None and preloaded:
        expanded = catalog.expand_with_companions(preloaded, max_depth=2)
        added = [s for s in expanded if s not in preloaded]
        if added:
            logger.info(
                "CockpitSkillLoad: coactivate-with expanded preloaded "
                "skills from %s by adding %s",
                preloaded,
                added,
            )
            preloaded = expanded
            # Mutate ctx.preloaded_skills in place so downstream consumers
            # (engine._activated_skills snapshot, prompt construction) see
            # the same expanded set.
            if hasattr(ctx, "preloaded_skills"):
                ctx.preloaded_skills[:] = expanded

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


@dataclass(frozen=True)
class _CachedSkillModule:
    """Per-file load result; reused across cockpit runs while file mtime is unchanged.

    The expensive parts of skill-tool loading — file I/O, ``importlib`` exec,
    ``inspect.signature`` introspection — are stable for a given source file.
    We cache them keyed on ``(absolute_path, mtime)`` so subsequent calls only
    rebuild the lightweight per-call wrapper closure that captures the
    current ``CockpitContext.visitor``.

    ``skip_reason`` is non-None when the file does NOT yield a usable tool
    (missing ``get_tool_definition`` / ``execute``, malformed schema,
    spec_from_file_location returned None). Cached "skip" entries avoid
    repeating the same diagnostic work on every cockpit run.
    """

    raw_tool_name: Optional[str]
    description: str
    parameters_schema: Dict[str, Any]
    execute_fn: Optional[Callable[..., Any]]
    execute_takes_visitor: bool
    skip_reason: Optional[str]


# Process-wide cache of loaded skill modules. Keyed on ``(absolute_path, mtime)``
# so a file edit produces a fresh entry automatically (mtime changes → cache
# miss → cold load). A simple dict is fine: modules don't accumulate (typical
# agents have <100 skill files) and entries cost ~1KB each.
_SKILL_MODULE_CACHE: Dict[Tuple[str, float], _CachedSkillModule] = {}
_SKILL_MODULE_CACHE_LOCK = threading.Lock()


def _file_mtime(file_path: Path) -> float:
    """Return file mtime, or 0.0 when stat fails."""
    try:
        return file_path.stat().st_mtime
    except OSError:
        return 0.0


def _import_skill_module(file_path: Path, mod_name: str) -> Any:
    """Import a single skill source file as a Python module.

    Wraps ``importlib.util`` so callers get a fresh module on cold load
    without leaving a partially-initialised module in ``sys.modules`` if
    exec fails.
    """
    spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
    if not spec or not spec.loader:
        raise ImportError(f"spec_from_file_location returned None for {file_path}")
    sys.modules.pop(mod_name, None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return module


def _load_or_get_cached_module(file_path: Path, prefix: str) -> _CachedSkillModule:
    """Resolve a skill source file to a ``_CachedSkillModule`` (cached).

    Cache key includes ``mtime`` so editing the file produces a fresh load
    on the next cockpit run without operator intervention. Errors raised
    during exec / ``get_tool_definition()`` propagate to the caller for
    the load report; everything else is captured as a ``skip_reason`` on
    the cached entry so the diagnostic work isn't repeated.
    """
    abs_path = str(file_path)
    mtime = _file_mtime(file_path)
    cache_key = (abs_path, mtime)
    cached = _SKILL_MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    name = file_path.stem
    mod_name = f"jvagent_cockpit_skill_{prefix}_{name}"

    module = _import_skill_module(file_path, mod_name)

    tool_def_fn = getattr(module, "get_tool_definition", None)
    execute_fn = getattr(module, "execute", None)

    if tool_def_fn is None or execute_fn is None:
        sys.modules.pop(mod_name, None)
        missing = []
        if tool_def_fn is None:
            missing.append("get_tool_definition")
        if execute_fn is None:
            missing.append("execute")
        cached = _CachedSkillModule(
            raw_tool_name=None,
            description="",
            parameters_schema={},
            execute_fn=None,
            execute_takes_visitor=False,
            skip_reason=f"module missing: {', '.join(missing)}",
        )
        _SKILL_MODULE_CACHE[cache_key] = cached
        return cached

    try:
        tool_def_dict = tool_def_fn()
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        # Don't cache exec failures — author may fix and reload.
        raise RuntimeError(f"get_tool_definition() raised: {exc}") from exc

    if not isinstance(tool_def_dict, dict):
        sys.modules.pop(mod_name, None)
        cached = _CachedSkillModule(
            raw_tool_name=None,
            description="",
            parameters_schema={},
            execute_fn=None,
            execute_takes_visitor=False,
            skip_reason="get_tool_definition() did not return a dict",
        )
        _SKILL_MODULE_CACHE[cache_key] = cached
        return cached

    fn_block = tool_def_dict.get("function", {})
    raw_tool_name = fn_block.get("name") or tool_def_dict.get("name")
    description = fn_block.get("description") or tool_def_dict.get("description", "")
    parameters = fn_block.get("parameters") or tool_def_dict.get(
        "parameters", {"type": "object", "properties": {}}
    )
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}

    if not raw_tool_name:
        sys.modules.pop(mod_name, None)
        cached = _CachedSkillModule(
            raw_tool_name=None,
            description="",
            parameters_schema={},
            execute_fn=None,
            execute_takes_visitor=False,
            skip_reason="tool definition missing name",
        )
        _SKILL_MODULE_CACHE[cache_key] = cached
        return cached

    # Pre-resolve the visitor-capability check so the per-call wrapper
    # doesn't have to call ``inspect.signature`` on every dispatch.
    try:
        sig = inspect.signature(execute_fn)
        execute_takes_visitor = "visitor" in sig.parameters
    except (TypeError, ValueError):
        execute_takes_visitor = False

    cached = _CachedSkillModule(
        raw_tool_name=str(raw_tool_name),
        description=str(description or ""),
        parameters_schema=parameters,
        execute_fn=execute_fn,
        execute_takes_visitor=execute_takes_visitor,
        skip_reason=None,
    )
    _SKILL_MODULE_CACHE[cache_key] = cached
    return cached


def clear_skill_module_cache() -> None:
    """Drop all cached skill-module load results.

    Call from test setup and from operator-triggered reload paths so the
    next cockpit run does a fresh import. Production code shouldn't need
    this — file mtime changes invalidate cache entries automatically.
    """
    with _SKILL_MODULE_CACHE_LOCK:
        _SKILL_MODULE_CACHE.clear()


def _load_tool_module(
    file_path: Any,
    prefix: str,
    allowed_tools: set,
    ctx: CockpitContext,
) -> "tuple[Optional[Tool], Optional[str]]":
    """Resolve a skill source file to a ``Tool`` instance for this cockpit run.

    Wraps the module-load cache so subsequent runs avoid the importlib /
    ``inspect.signature`` cost. Returns ``(tool, skip_reason)`` with
    exactly one populated.
    """
    cached = _load_or_get_cached_module(Path(file_path), prefix)

    if cached.skip_reason is not None:
        return None, cached.skip_reason

    raw_tool_name = cached.raw_tool_name or ""
    qualified_name = f"{prefix}__{raw_tool_name}"
    # ``allowed_tools`` accepts either the raw frontmatter tool name or the
    # qualified ``{prefix}__{name}`` so skill authors can declare entries
    # in either form. The cached module data is shared across allowlists
    # (different bundles using the same file see the same cache entry);
    # the filter therefore lives here, in the per-call wrapper.
    if (
        allowed_tools
        and raw_tool_name not in allowed_tools
        and qualified_name not in allowed_tools
    ):
        return None, (
            f"name '{raw_tool_name}' not in allowed_tools "
            f"(checked raw '{raw_tool_name}' and qualified '{qualified_name}')"
        )

    execute_fn = cached.execute_fn
    takes_visitor = cached.execute_takes_visitor

    async def _wrapped_execute(**kwargs: Any) -> Any:
        if takes_visitor:
            result = execute_fn(kwargs, visitor=ctx.visitor)
        else:
            result = execute_fn(kwargs)
        if inspect.isawaitable(result):
            result = await result
        # Return the raw result. ``Tool.call`` (jvagent/tooling/tool.py)
        # handles serialization centrally — strings pass through, dicts
        # / lists / other JSON-compatible values get json.dumps'd, and
        # ToolResult instances pass through. Doing ``str(result)`` here
        # produces a Python repr (single-quoted ``{'key': 'value'}``)
        # which is NOT valid JSON, breaks downstream consumers that
        # try to json.loads the tool output (notably the SPEC §7.3
        # tool_result envelope path on the cockpit, which now feeds
        # the actual dict to streaming consumers), and bloats the
        # model's context with quoted gibberish. Centralised
        # serialization in Tool.call is the right layer.
        return result

    return (
        Tool(
            name=qualified_name,
            description=cached.description,
            parameters_schema=cached.parameters_schema,
            execute=_wrapped_execute,
        ),
        None,
    )
