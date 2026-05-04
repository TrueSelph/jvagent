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


class AgentInteractToolExecutor(ToolExecutor):
    """``ToolExecutor`` with idempotent ``register_skill_bundle`` (mid-loop discovery)."""

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
                        # Pre-activate so the plan-first gate allows
                        # substantive tools immediately (shortcut path).
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
    return await engine.run_to_completion(ctx)
