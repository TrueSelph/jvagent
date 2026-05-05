"""Agentic skill loop for AgentInteract (subclasses ``SkillAction`` for prepare_run)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.agent_interact.skill.context import AgentInteractSkillRunContext
from jvagent.action.agent_interact.skill.native_tools import (
    register_converse_skill_tool,
)
from jvagent.action.agent_interact.skill.shim import AgentInteractVisitorShim
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.skill_action import SkillAction
from jvagent.action.skill.skill_action_contracts import SkillRunContext, SkillRunResult
from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

_TOOL_ARGS_PREVIEW_LIMIT = 500
_TOOL_RESULT_PREVIEW_LIMIT = 500


class AgentInteractToolExecutor(ToolExecutor):
    """``ToolExecutor`` with idempotent ``register_skill_bundle`` and dispatch logging."""

    def __init__(
        self,
        call_timeout: float = 60.0,
        validate_calls: bool = True,
        max_concurrent_calls: int = 5,
        sanitize_errors: bool = True,
        allowed_tool_paths: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            call_timeout=call_timeout,
            validate_calls=validate_calls,
            max_concurrent_calls=max_concurrent_calls,
            sanitize_errors=sanitize_errors,
            allowed_tool_paths=allowed_tool_paths,
        )
        self._registered_skill_names: Set[str] = set()
        self.dispatch_log: List[Dict[str, Any]] = []

    def register_skill_bundle(
        self,
        skill_name: str,
        dir_path: str,
        tool_files: Optional[List[str]] = None,
        allowed_tools: Optional[List[str]] = None,
        exports: Optional[List[str]] = None,
        imports: Optional[List[str]] = None,
    ) -> None:
        if skill_name in self._registered_skill_names:
            logger.debug(
                "AgentInteractToolExecutor: skill '%s' already registered, skipping",
                skill_name,
            )
            return
        super().register_skill_bundle(
            skill_name=skill_name,
            dir_path=dir_path,
            tool_files=tool_files,
            allowed_tools=allowed_tools,
            exports=exports,
            imports=imports,
        )
        self._registered_skill_names.add(skill_name)

    def unregister_skill_bundle(self, skill_name: str) -> List[str]:
        out = super().unregister_skill_bundle(skill_name)
        self._registered_skill_names.discard(skill_name)
        return out

    async def dispatch(
        self, tool_calls: List[Dict[str, Any]], visitor: Any = None
    ) -> List[Dict[str, Any]]:
        results = await super().dispatch(tool_calls, visitor=visitor)
        for tc, result in zip(tool_calls, results):
            fn = tc.get("function", {})
            self.dispatch_log.append(
                {
                    "tool_call_id": tc.get("id", "") or result.get("tool_call_id", ""),
                    "tool_name": fn.get("name", "unknown"),
                    "arguments": (fn.get("arguments") or "")[:_TOOL_ARGS_PREVIEW_LIMIT],
                    "result_preview": (result.get("content") or "")[
                        :_TOOL_RESULT_PREVIEW_LIMIT
                    ],
                }
            )
        return results


class _ObservingTaskHandle:
    """Wraps a ``TaskHandle`` to enrich ``tool_result`` step recordings."""

    def __init__(self, real_handle: Any, executor: AgentInteractToolExecutor) -> None:
        object.__setattr__(self, "_real", real_handle)
        object.__setattr__(self, "_executor", executor)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real"), name)

    async def add_event(
        self,
        event_type: str,
        iteration: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if event_type == "tool_result" and details:
            executor = object.__getattribute__(self, "_executor")
            if isinstance(executor, AgentInteractToolExecutor):
                details = _enrich_tool_result(details, executor)
        return await object.__getattribute__(self, "_real").add_event(
            event_type, iteration, details
        )


class _ObservingTaskStore:
    """Wraps a ``TaskStore`` so ``track()`` returns observing handles."""

    def __init__(self, real_store: Any, skill_state: dict) -> None:
        self._real = real_store
        self._skill_state = skill_state

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def track(self, **kwargs: Any) -> Any:
        inner = self._real.track(**kwargs)
        skill_state = self._skill_state
        real_aenter = inner.__aenter__
        real_aexit = inner.__aexit__

        class _Ctx:
            async def __aenter__(_self):  # noqa: N805
                handle = await real_aenter()
                executor = skill_state.get("tool_executor") if skill_state else None
                if isinstance(executor, AgentInteractToolExecutor):
                    return _ObservingTaskHandle(handle, executor)
                return handle

            async def __aexit__(_self, *args):  # noqa: N805
                return await real_aexit(*args)

        return _Ctx()


def _enrich_tool_result(
    details: Dict[str, Any], executor: AgentInteractToolExecutor
) -> Dict[str, Any]:
    if not executor.dispatch_log:
        return details
    args_by_id: Dict[str, Dict[str, Any]] = {}
    for entry in executor.dispatch_log:
        tc_id = entry.get("tool_call_id", "")
        if entry.get("tool_name") and tc_id:
            args_by_id[tc_id] = entry
    enriched_results: List[Dict[str, Any]] = []
    for r in details.get("results", []):
        r2 = dict(r)
        tc_id = r.get("tool_call_id", "")
        entry = args_by_id.get(tc_id)
        if entry:
            r2["tool_name"] = entry["tool_name"]
            r2["tool_args"] = entry["arguments"]
            if len(r2.get("content_preview", "")) < len(entry["result_preview"]):
                r2["content_preview"] = entry["result_preview"]
        enriched_results.append(r2)
    details = dict(details)
    details["results"] = enriched_results
    return details


class AgentInteractSkillAction(SkillAction):
    """``SkillAction`` with preload-aware ``prepare_run`` and idempotent tool registry."""

    async def prepare_run(
        self, ctx: SkillRunContext
    ) -> Tuple[ToolExecutor, Dict[str, Any], SkillCatalog]:
        cfg = ctx.config

        action_resolver = ActionResolver(ctx.agent) if ctx.agent else None

        _visitor_shim = AgentInteractVisitorShim(
            ctx.agent,
            action_resolver,
            user_id=ctx.user_id,
            conversation=ctx.conversation,
            interaction=ctx.interaction,
            session_id=ctx.session_id,
            response_bus=ctx.response_bus,
            channel=ctx.channel,
        )
        skill_catalog = await SkillCatalog.discover(
            visitor=_visitor_shim,
            skills_selector=cfg.skills,
            skills_source=cfg.skills_source,
            denied_skills=cfg.denied_skills or None,
        )
        discovered_skills = skill_catalog.skills

        local_paths: List[str] = []
        if cfg.local_tools_path:
            local_paths.append(cfg.local_tools_path)

        tool_executor = AgentInteractToolExecutor(
            call_timeout=cfg.call_timeout_seconds,
            sanitize_errors=True,
        )
        await tool_executor.initialize(
            visitor=_visitor_shim,
            tool_servers=cfg.tool_servers,
            local_tools_paths=local_paths,
        )

        preloaded: List[str] = []
        if isinstance(ctx, AgentInteractSkillRunContext):
            preloaded = list(ctx.preloaded_skills or [])

        if not skill_catalog.is_empty:
            if preloaded:
                for skill_name in preloaded:
                    if skill_name in discovered_skills:
                        skill_data = discovered_skills[skill_name]
                        tool_executor.register_skill_bundle(
                            skill_name=skill_name,
                            dir_path=skill_data["dir"],
                            tool_files=skill_data.get("tool_files", []),
                            allowed_tools=skill_data.get("allowed_tools", []),
                            exports=skill_data.get("exports", []),
                            imports=skill_data.get("imports", []),
                        )
                        try:
                            await tool_executor.activate_skill(
                                skill_name,
                                action_resolver=action_resolver,
                                visitor=_visitor_shim,
                            )
                        except Exception as exc:
                            logger.warning(
                                "AgentInteractSkillAction: pre-activation of '%s' failed: %s",
                                skill_name,
                                exc,
                            )
            else:
                for skill_name, skill_data in discovered_skills.items():
                    tool_executor.register_skill_bundle(
                        skill_name=skill_name,
                        dir_path=skill_data["dir"],
                        tool_files=skill_data.get("tool_files", []),
                        allowed_tools=skill_data.get("allowed_tools", []),
                        exports=skill_data.get("exports", []),
                        imports=skill_data.get("imports", []),
                    )

            tool_executor.register_dynamic_tool(
                name="read_skill",
                tool_def_dict={
                    "name": "read_skill",
                    "description": (
                        "Read the full instructions/SOP for a LOCAL skill already "
                        "installed in this agent."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "The name of the skill to read.",
                            }
                        },
                        "required": ["skill_name"],
                    },
                },
                handler=self._make_read_skill_handler(
                    discovered_skills,
                    skill_catalog,
                    tool_executor,
                    action_resolver,
                    cfg,
                    visitor=_visitor_shim,
                ),
            )

        # Always-on catalog helpers + native converse (no skill-bundle bootstrap delay).
        self._register_skill_helper_tools(
            tool_executor, skill_catalog, discovered_skills, ctx
        )
        register_converse_skill_tool(tool_executor, ctx)

        if not tool_executor.get_tool_names():
            logger.warning(
                "AgentInteractSkillAction: No tools available; proceeding in reasoning-only mode"
            )

        if not skill_catalog.is_empty:
            preflight_failures = await skill_catalog.preflight_check(
                action_resolver=action_resolver,
                tool_executor=tool_executor,
            )
            if preflight_failures:
                for pf in preflight_failures:
                    skill_name = pf.get("skill") or pf.get("name") or "unknown"
                    kind = pf.get("kind") or pf.get("type") or "unknown"
                    detail = pf.get("detail") or pf.get("message") or str(pf)
                    logger.warning(
                        "AgentInteractSkillAction: preflight failure [%s] for skill '%s': %s",
                        kind,
                        skill_name,
                        detail,
                    )
                context = getattr(ctx.conversation, "context", None)
                if isinstance(context, dict):
                    context["_skill_preflight_failures"] = preflight_failures

        return tool_executor, discovered_skills, skill_catalog


async def run_agentic_skill_loop(ctx: SkillRunContext) -> SkillRunResult:
    """Run the full skill loop using AgentInteract-specific ``prepare_run``."""
    engine = AgentInteractSkillAction()

    skill_state = ctx.skill_state if ctx.skill_state is not None else {}
    original_task_store = ctx.task_store
    ctx.task_store = _ObservingTaskStore(original_task_store, skill_state)

    try:
        result = await engine.run_to_completion(ctx)
    finally:
        ctx.task_store = original_task_store

    return result
