"""SkillAction: reusable reasoning core for long-running, skill-based agents.

SkillAction encapsulates the think-act-observe loop, tool execution, skill
management, and task tracking in a single runtime class that any Action (or
service) can invoke directly — without routing through the interact subsystem.

Programmatic interface
----------------------
Other actions call ``SkillAction`` by instantiating it and calling
``run_to_completion(ctx)``.  The ``SkillRunContext`` bundles every dependency
the engine needs; no ``InteractWalker`` coupling.

Key callable methods
~~~~~~~~~~~~~~~~~~~~
- ``run_to_completion(ctx)``       – full setup → loop → teardown → result
- ``prepare_run(ctx)``             – set up tool executor and skill catalog only
- ``run_iteration(run_state)``     – one think-act-observe iteration (low-level)
- ``finalize_result(run_state, candidate)`` – final review + grounding pass

``SkillInteractAction`` uses this as its engine via composition; see
``skill_interact_action.py`` for the interact-subsystem adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.model.language.base import ReasoningModelConfig
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.context_compactor import CompactorConfig, ContextCompactor
from jvagent.action.skill.loop_checkpoint import CheckpointStore, LoopCheckpoint
from jvagent.action.skill.loop_context import LoopContext, LoopContextConfig
from jvagent.action.skill.prompts import (
    ERROR_ANNOUNCE_TEMPLATE,
    FINAL_REVIEW_PROMPT,
    FINAL_REVIEW_PROMPT_WITH_PLAN,
    FORCED_TERMINATION_PROMPT_ITER_CAP,
    FORCED_TERMINATION_PROMPT_NO_CHECKLIST,
    FORCED_TERMINATION_PROMPT_STUCK,
    FORCED_TERMINATION_PROMPT_TEMPLATE,
    FORCED_TERMINATION_PROMPT_TIME_CAP,
    GROUNDING_INSTRUCTION_TEMPLATE,
    INCOMPLETE_STEPS_PRE_RESPONSE_PROMPT,
    LIST_SKILLS_TOOL_DESCRIPTION,
    MONOLOGUE_OPENERS,
    MONOLOGUE_RESULT_ERR,
    MONOLOGUE_RESULT_OK,
    PENDING_STEPS_NUDGE_PROMPT,
    PENDING_STEPS_NUDGE_PROMPT_FINAL,
    PLAN_SKILLS_TOOL_DESCRIPTION,
    PROGRESS_CHECK_PROMPT_TEMPLATE,
    READ_SKILL_PLAN_STEPS_HINT,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_AGENT_SYSTEM_PROMPT,
    SKILL_FIRST_RETRY_PROMPT,
    SKILL_SEARCH_TOOL_DESCRIPTION,
    STATUS_PLAN_CREATED,
    STATUS_STEP_COMPLETED,
    STATUS_STEP_NEXT,
    STATUS_STEP_SKIPPED_WITH_NEXT,
    STUCK_DETECTION_PROMPT,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
    TOOL_RESULT_ANNOUNCE_TEMPLATE,
    plan_final_status_message,
)
from jvagent.action.skill.recovery_policy import FailureRecord, RecoveryPolicy
from jvagent.action.skill.skill_action_contracts import (
    LoopPhase,
    SkillRunConfig,
    SkillRunContext,
    SkillRunResult,
    TerminationReason,
)
from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.stuck_detector import StuckDetector, StuckDetectorConfig
from jvagent.action.skill.task_plan import InLoopTaskPlan, TaskStep
from jvagent.action.skill.tool_executor import ToolExecutor
from jvagent.memory.evidence_log import EvidenceLog

logger = logging.getLogger(__name__)


@dataclass
class _NoToolResult:
    """Result returned by ``_handle_no_tool_response``."""

    loop_phase: LoopPhase
    termination_reason: TerminationReason
    final_response: str
    candidate_response: Optional[str]
    best_candidate: Optional[str]
    task_nudges: int
    task_nudges_total: int
    skill_first_retries: int
    retry_nudges: int
    control: str  # "break" or "continue"


@dataclass
class _ToolCallResult:
    """Result returned by ``_handle_tool_calls``."""

    loop_phase: LoopPhase
    termination_reason: TerminationReason
    final_response: str
    messages: List[Dict[str, Any]]
    task_nudges: int
    result_attributions: List[Dict[str, Any]]
    control: str  # "break" or empty string (fall through)


# Built-in coordination / catalog navigation tools — not considered "real" tool evidence.
_SKILL_HELPER_TOOL_NAMES: frozenset = frozenset(
    (
        "list_skills",
        "skill_search",
        "plan_skills",
        "read_skill",
        "preview_skill",
        "task_tracker",
    )
)

# Shown when plan_first is enabled, no plan exists yet, and a substantive tool is blocked.
PLAN_FIRST_BLOCKED_TOOL_MESSAGE: str = (
    "Error: Create an in-loop task plan before using this tool. Call `task_tracker` with "
    '`action="create"` and a `steps` array describing the parts of the work (a one-item list '
    "is fine for a single straightforward request), then invoke this tool again."
)


class SkillAction:
    """Reusable reasoning engine for skill-based, long-running agent tasks.

    This class is intentionally NOT a jvspatial Node/Action — it is a pure
    Python runtime helper instantiated by its callers.  This keeps the loop
    logic fully decoupled from the interact subsystem and testable in
    isolation.

    Usage::

        ctx = SkillRunContext(
            utterance=...,
            conversation=...,
            model_action=...,
            task_service=...,
            config=SkillRunConfig(...),
        )
        engine = SkillAction()
        result = await engine.run_to_completion(ctx)
        print(result.final_response)
    """

    # ---------------------------------------------------------------------------
    # Public programmatic interface
    # ---------------------------------------------------------------------------

    async def run_to_completion(self, ctx: SkillRunContext) -> SkillRunResult:
        """Execute a full skill run: setup → loop → teardown → result.

        This is the primary entry point for callers.  It handles skill
        discovery, tool registration, task tracking, the agentic loop, and
        final-response delivery.

        Args:
            ctx: Full run context (see SkillRunContext).

        Returns:
            SkillRunResult with response, termination reason, and metadata.
        """
        tool_executor: Optional[ToolExecutor] = None
        task_handle = None
        try:
            # 1. Resolve skills + build tool executor
            tool_executor, discovered_skills, skill_catalog = await self.prepare_run(
                ctx
            )

            # Wire hot-reload state so refresh_skills() can reach live objects during the loop
            if ctx.skill_state is not None:
                ctx.skill_state["tool_executor"] = tool_executor
                ctx.skill_state["discovered_skills"] = discovered_skills
                ctx.skill_state["skill_catalog"] = skill_catalog

            # 2. Open structured task record
            task_description = (
                f"Agentic task: {ctx.utterance[:100]}"
                if ctx.utterance
                else "Agentic task"
            )
            async with ctx.task_service.track(
                description=task_description,
                task_type="AGENTIC_LOOP",
                action_name="SkillAction",
                metadata=self._initial_task_metadata(ctx),
            ) as task_handle:

                # 3. Build system prompt with optional persona injection
                system_prompt = SKILL_AGENT_SYSTEM_PROMPT.format(
                    agent_name=ctx.agent_name,
                    agent_description=ctx.agent_description,
                )
                if not ctx.config.plan_first:
                    system_prompt += (
                        "\n\nOverride: Skip all plan-first behavior, summaries, and preambles. "
                        "Go straight to execution—silently call tools in the same turn. "
                        "Do not narrate your intent or explain what you are about to do."
                    )
                if not ctx.config.strict_grounding:
                    system_prompt += (
                        "\n\nOverride: You may answer with best-effort general "
                        "reasoning when tool evidence is unavailable."
                    )

                skill_index_section = None
                if discovered_skills:
                    skill_index_section = SkillCatalog(
                        discovered_skills
                    ).render_system_prompt_section()

                # 4. Run the agentic loop
                result = await self._run_loop(
                    ctx=ctx,
                    tool_executor=tool_executor,
                    task_handle=task_handle,
                    discovered_skills=discovered_skills,
                    system_prompt=system_prompt,
                    skill_index_section=skill_index_section,
                )

                await task_handle.update_metadata(
                    activated_skills=sorted(tool_executor.activated_skills),
                    stuck_corrections=result.stuck_corrections,
                )
                await task_handle.complete(
                    status=result.termination_reason.value,
                    summary=(
                        result.final_response[:200] if result.final_response else None
                    ),
                )
                return result

        except Exception as exc:
            logger.error("SkillAction.run_to_completion failed: %s", exc, exc_info=True)
            if task_handle:
                try:
                    await task_handle.fail(error=str(exc))
                except Exception:
                    pass
            return SkillRunResult(
                final_response="I was unable to complete the task due to an unexpected error.",
                termination_reason=TerminationReason.ERROR,
                stuck_corrections=0,
                result_attributions=[],
                iterations=0,
                duration_seconds=0.0,
                task_id=None,
                activated_skills=[],
                metadata={"error": str(exc)},
            )
        finally:
            if tool_executor:
                try:
                    await tool_executor.cleanup()
                except Exception as cleanup_err:
                    logger.warning("SkillAction: tool cleanup failed: %s", cleanup_err)

    async def prepare_run(
        self, ctx: SkillRunContext
    ) -> Tuple[ToolExecutor, Dict[str, Any], SkillCatalog]:
        """Discover skills and initialize the tool executor.

        Separated from ``run_to_completion`` so callers that want fine-grained
        control can inspect discovered skills before starting the loop.

        Returns:
            Tuple of (ToolExecutor, discovered_skills dict, SkillCatalog).
        """
        cfg = ctx.config

        # Action resolver for skill requirements validation
        action_resolver = ActionResolver(ctx.agent) if ctx.agent else None

        # Skill discovery
        # Build a minimal visitor-like object for SkillCatalog (which expects visitor)
        _visitor_shim = _AgentShim(
            ctx.agent,
            action_resolver,
            user_id=ctx.user_id,
            conversation=ctx.conversation,
            interaction=ctx.interaction,
            session_id=ctx.session_id,
        )
        skill_catalog = await SkillCatalog.discover(
            visitor=_visitor_shim,
            skills_selector=cfg.skills,
            skills_source=cfg.skills_source,
            denied_skills=cfg.denied_skills or None,
        )
        discovered_skills = skill_catalog.skills

        # Tool executor
        local_paths: List[str] = []
        if cfg.local_tools_path:
            local_paths.append(cfg.local_tools_path)

        tool_executor = ToolExecutor(
            call_timeout=cfg.call_timeout_seconds,
            sanitize_errors=True,
        )
        await tool_executor.initialize(
            visitor=_visitor_shim,
            tool_servers=cfg.tool_servers,
            local_tools_paths=local_paths,
        )

        # Register skill bundles
        if not skill_catalog.is_empty:
            for skill_name, skill_data in discovered_skills.items():
                tool_executor.register_skill_bundle(
                    skill_name=skill_name,
                    dir_path=skill_data["dir"],
                    tool_files=skill_data.get("tool_files", []),
                    allowed_tools=skill_data.get("allowed_tools", []),
                    exports=skill_data.get("exports", []),
                    imports=skill_data.get("imports", []),
                )

            # read_skill dynamic tool
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

            if cfg.enable_skill_helper_tools:
                self._register_skill_helper_tools(
                    tool_executor, skill_catalog, discovered_skills, ctx
                )

        if not tool_executor.get_tool_names():
            logger.warning(
                "SkillAction: No tools available; proceeding in reasoning-only mode"
            )

        # --- Skill preflight: deterministic capability check before first model call ---
        if not skill_catalog.is_empty:
            preflight_failures = await skill_catalog.preflight_check(
                action_resolver=action_resolver,
                tool_executor=tool_executor,
            )
            if preflight_failures:
                # Emit a WARNING per failure so operators can observe which skills
                # fail preflight in production without inspecting conversation.context (3.6).
                for pf in preflight_failures:
                    skill_name = pf.get("skill") or pf.get("name") or "unknown"
                    kind = pf.get("kind") or pf.get("type") or "unknown"
                    detail = pf.get("detail") or pf.get("message") or str(pf)
                    logger.warning(
                        "SkillAction: preflight failure [%s] for skill '%s': %s",
                        kind,
                        skill_name,
                        detail,
                    )
                # Also persist into conversation context for downstream inspection.
                context = getattr(ctx.conversation, "context", None)
                if isinstance(context, dict):
                    context["_skill_preflight_failures"] = preflight_failures

        return tool_executor, discovered_skills, skill_catalog

    async def run_iteration(
        self,
        *,
        ctx: SkillRunContext,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        iteration: int,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
    ) -> Any:
        """Execute a single model call for fine-grained loop control.

        Returns the raw ModelActionResult.  Callers that drive the loop
        step-by-step (e.g. background workers) can use this instead of
        ``run_to_completion``.
        """
        return await self._call_model(
            messages=messages,
            tools=tools,
            ctx=ctx,
            base_model_kwargs=base_model_kwargs,
            reasoning_cfg=reasoning_cfg,
            loop_iteration=iteration,
        )

    async def finalize_result(
        self,
        *,
        ctx: SkillRunContext,
        messages: List[Dict[str, Any]],
        candidate: str,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
    ) -> str:
        """Run optional final-review pass then return grounded response."""
        return await self._final_review_pass(
            messages=messages,
            candidate_response=candidate,
            ctx=ctx,
            base_model_kwargs=base_model_kwargs,
            reasoning_cfg=reasoning_cfg,
        )

    # ---------------------------------------------------------------------------
    # Internal loop
    # ---------------------------------------------------------------------------

    async def _run_loop(
        self,
        *,
        ctx: SkillRunContext,
        tool_executor: ToolExecutor,
        task_handle: Any,
        discovered_skills: Dict[str, Any],
        system_prompt: str,
        skill_index_section: Optional[str],
    ) -> SkillRunResult:
        cfg = ctx.config
        base_model_kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "temperature": cfg.model_temperature,
            "max_tokens": cfg.model_max_tokens,
        }
        reasoning_cfg = self._build_reasoning_cfg(cfg)

        loop_ctx = LoopContext(
            LoopContextConfig(
                max_full_tool_results=cfg.max_full_tool_results,
                max_tool_result_tokens=cfg.max_tool_result_tokens,
                tool_result_truncation_chars=cfg.tool_result_truncation_chars,
                history_limit=cfg.history_limit,
            )
        )
        compactor = ContextCompactor(
            CompactorConfig(
                max_full_tool_results=cfg.max_full_tool_results,
                max_tool_result_tokens=cfg.max_tool_result_tokens,
                tool_result_truncation_chars=cfg.tool_result_truncation_chars,
            )
        )
        evidence_log = EvidenceLog()
        checkpoint_store = CheckpointStore(ctx.conversation)

        messages = await loop_ctx.build_initial_messages(
            system_prompt=system_prompt,
            utterance=ctx.utterance,
            conversation=ctx.conversation,
            interaction=ctx.interaction,
            skill_index_section=skill_index_section,
        )

        loop_start = time.monotonic()
        iteration = 0
        final_response = ""
        termination_reason = TerminationReason.COMPLETED
        loop_phase = LoopPhase.INIT

        stuck_detector = StuckDetector(
            StuckDetectorConfig(
                window_size=max(1, int(cfg.stuck_detection_window or 1)),
                max_corrections=cfg.max_midcourse_corrections,
                intent_jaccard_threshold=cfg.stuck_intent_jaccard_threshold,
            )
        )

        skill_first_retries = 0
        task_nudges = 0
        task_nudges_total = 0
        result_attributions: List[Dict[str, Any]] = []

        meta_extra: List[str] = list(cfg.meta_intent_patterns or [])
        is_meta_utterance = SkillCatalog.is_meta_intent(
            ctx.utterance or "", meta_extra or None
        )
        await task_handle.update_metadata(meta_intent_detected=is_meta_utterance)

        task_plan_state: Dict[str, Any] = {
            "plan": None,
            # Counts non-helper tool calls since the last task_tracker complete/skip.
            # Used to warn the model if it tries to complete a step without doing any work.
            "tool_calls_since_complete": 0,
        }
        tool_executor.register_dynamic_tool(
            name="task_tracker",
            tool_def_dict={
                "name": "task_tracker",
                "description": (
                    "Create, read, complete, skip, or append steps in the in-loop task plan. "
                    "For multi-step tasks, create the plan first, then complete each step "
                    "before moving to the next. Use skip (with a reason) when a step "
                    "cannot be performed so the plan can advance. "
                    "Use append to add newly-discovered steps without losing prior history."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "read", "complete", "skip", "append"],
                        },
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Ordered step descriptions used when action=create or action=append. "
                                "For append, new steps are added after the last existing step."
                            ),
                        },
                        "step_id": {
                            "type": "integer",
                            "description": "1-based step id used when action=complete or action=skip.",
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "Required when action=skip. Explain why the step cannot be "
                                "performed so the reason appears in the final response."
                            ),
                        },
                    },
                    "required": ["action"],
                },
            },
            handler=self._make_task_tracker_handler(
                ctx=ctx,
                cfg=cfg,
                task_plan_state=task_plan_state,
                task_handle=task_handle,
                iteration_getter=lambda: iteration,
                review_enabled=cfg.final_review,
            ),
        )

        tools_ever_called: Set[str] = set()
        nontrivial_tools_ever_called: Set[str] = set()
        candidate_response: Optional[str] = None
        best_candidate: Optional[str] = None
        thinking_tokens_total: int = 0
        retry_nudges: int = 0

        recovery_policy = RecoveryPolicy()

        while iteration < cfg.max_iterations:
            elapsed = time.monotonic() - loop_start
            if elapsed >= cfg.max_duration_seconds:
                loop_phase = LoopPhase.TERMINATE
                final_response = await self._force_termination(
                    messages,
                    tool_executor.get_tools_list(),
                    ctx,
                    base_model_kwargs,
                    reasoning_cfg,
                    checklist=self._task_plan_pending_checklist(
                        task_plan_state["plan"]
                    ),
                    termination_cause="time_cap",
                )
                termination_reason = TerminationReason.TIME_CAP
                break

            iteration += 1
            loop_phase = LoopPhase.MODEL_CALL

            # Checkpoint before model call
            if cfg.enable_checkpoints:
                ckpt = LoopCheckpoint(
                    iteration=iteration,
                    phase=loop_phase.value,
                    elapsed_seconds=elapsed,
                    pending_tool_names=[],
                    termination_reason_candidate=termination_reason.value,
                )
                await checkpoint_store.save(ckpt)

            # Periodic progress self-assessment
            progress_interval = cfg.progress_check_interval
            if (
                progress_interval > 0
                and iteration > 1
                and iteration % progress_interval == 0
            ):
                messages.append(
                    {
                        "role": "user",
                        "content": PROGRESS_CHECK_PROMPT_TEMPLATE.format(
                            iteration=iteration,
                            max_iterations=cfg.max_iterations,
                        ),
                    }
                )

            # When the plan is finished (no pending/in_progress steps remain) but
            # some steps were not done, inject a one-time honesty reminder before
            # the model produces its final response.  This prevents fabricated
            # "I completed X" claims for steps the model actually skipped.
            _pre_warn_plan = task_plan_state.get("plan")
            if (
                _pre_warn_plan is not None
                and not _pre_warn_plan.has_pending_steps()
                and not task_plan_state.get("_incomplete_pre_warned")
            ):
                _incomplete_steps = [
                    s for s in _pre_warn_plan.steps if s.status != "done"
                ]
                if _incomplete_steps:
                    _warn_lines = []
                    for s in _incomplete_steps:
                        detail = f"[{s.status}]"
                        if s.skip_reason:
                            detail += f" reason: {s.skip_reason}"
                        _warn_lines.append(f"- Step {s.id}: {s.description} {detail}")
                    messages.append(
                        {
                            "role": "user",
                            "content": INCOMPLETE_STEPS_PRE_RESPONSE_PROMPT.format(
                                incomplete_list="\n".join(_warn_lines)
                            ),
                        }
                    )
                    task_plan_state["_incomplete_pre_warned"] = True

            tools = tool_executor.get_tools_list()
            try:
                model_result = await self._call_model(
                    messages=messages,
                    tools=tools,
                    ctx=ctx,
                    base_model_kwargs=base_model_kwargs,
                    reasoning_cfg=reasoning_cfg,
                    loop_iteration=iteration,
                )
            except Exception as model_exc:
                failure = FailureRecord(
                    iteration=iteration,
                    phase=loop_phase.value,
                    error=str(model_exc),
                    recoverable=recovery_policy.is_recoverable(model_exc),
                )
                decision = recovery_policy.decide(failure)
                logger.warning(
                    "SkillAction: model call failed at iter %d (%s): %s → %s",
                    iteration,
                    loop_phase.value,
                    model_exc,
                    decision.action,
                )
                if decision.action == "terminate":
                    termination_reason = TerminationReason.ERROR
                    final_response = await self._force_termination(
                        messages,
                        tools,
                        ctx,
                        base_model_kwargs,
                        reasoning_cfg,
                        checklist=self._task_plan_pending_checklist(
                            task_plan_state["plan"]
                        ),
                        termination_cause="iter_cap",
                    )
                    break
                # retry: apply backoff, then continue (messages unchanged)
                if decision.delay_seconds > 0:
                    await asyncio.sleep(decision.delay_seconds)
                await task_handle.record_step(
                    "model_error",
                    iteration=iteration,
                    details={
                        "error": str(model_exc),
                        "action": decision.action,
                        "delay_seconds": decision.delay_seconds,
                    },
                )
                continue

            tok = self._resolve_thinking_token_count(model_result)
            thinking_tokens_total += tok
            await task_handle.update_metadata(
                thinking_tokens_used=thinking_tokens_total
            )
            await task_handle.record_step(
                "thinking",
                iteration=iteration,
                details={"tokens": tok},
            )

            # ---- No tool calls → candidate response ----
            if not model_result.tool_calls:
                r = await self._handle_no_tool_response(
                    model_result=model_result,
                    messages=messages,
                    cfg=cfg,
                    ctx=ctx,
                    task_plan_state=task_plan_state,
                    task_handle=task_handle,
                    tool_executor=tool_executor,
                    base_model_kwargs=base_model_kwargs,
                    reasoning_cfg=reasoning_cfg,
                    discovered_skills=discovered_skills,
                    iteration=iteration,
                    loop_phase=loop_phase,
                    termination_reason=termination_reason,
                    final_response=final_response,
                    candidate_response=candidate_response,
                    best_candidate=best_candidate,
                    task_nudges=task_nudges,
                    task_nudges_total=task_nudges_total,
                    skill_first_retries=skill_first_retries,
                    retry_nudges=retry_nudges,
                    tools_ever_called=tools_ever_called,
                    nontrivial_tools_ever_called=nontrivial_tools_ever_called,
                )
                loop_phase = r.loop_phase
                termination_reason = r.termination_reason
                final_response = r.final_response
                candidate_response = r.candidate_response
                best_candidate = r.best_candidate
                task_nudges = r.task_nudges
                task_nudges_total = r.task_nudges_total
                skill_first_retries = r.skill_first_retries
                retry_nudges = r.retry_nudges
                if r.control == "break":
                    break
                continue  # all non-break no-tool paths end in continue

            # ---- Tool calls ----
            r2 = await self._handle_tool_calls(
                model_result=model_result,
                messages=messages,
                cfg=cfg,
                ctx=ctx,
                task_plan_state=task_plan_state,
                task_handle=task_handle,
                tool_executor=tool_executor,
                base_model_kwargs=base_model_kwargs,
                reasoning_cfg=reasoning_cfg,
                loop_start=loop_start,
                iteration=iteration,
                is_meta_utterance=is_meta_utterance,
                stuck_detector=stuck_detector,
                checkpoint_store=checkpoint_store,
                evidence_log=evidence_log,
                compactor=compactor,
                result_attributions=result_attributions,
                loop_phase=loop_phase,
                termination_reason=termination_reason,
                final_response=final_response,
                task_nudges=task_nudges,
                tools_ever_called=tools_ever_called,
                nontrivial_tools_ever_called=nontrivial_tools_ever_called,
            )
            messages = r2.messages
            loop_phase = r2.loop_phase
            termination_reason = r2.termination_reason
            final_response = r2.final_response
            task_nudges = r2.task_nudges
            result_attributions = r2.result_attributions
            if r2.control == "break":
                break

        # ---- Post-loop handling ----
        if (
            not final_response
            and termination_reason == TerminationReason.COMPLETED
            and iteration >= cfg.max_iterations
        ):
            loop_phase = LoopPhase.TERMINATE
            final_response = await self._force_termination(
                messages,
                tool_executor.get_tools_list(),
                ctx,
                base_model_kwargs,
                reasoning_cfg,
                checklist=self._task_plan_pending_checklist(task_plan_state["plan"]),
                termination_cause="iter_cap",
            )
            termination_reason = TerminationReason.ITER_CAP

        if not final_response:
            final_response = (
                "I was unable to complete the task within the allowed steps."
            )
            if termination_reason == TerminationReason.COMPLETED:
                termination_reason = TerminationReason.ITER_CAP
        elif cfg.final_review:
            loop_phase = LoopPhase.FINALIZE
            if is_meta_utterance:
                await task_handle.record_step(
                    "final_review_skipped",
                    iteration=iteration,
                    details={"reason": "meta_intent_utterance"},
                )
            elif not nontrivial_tools_ever_called:
                # Skip the grounding review for conversational turns where no
                # tools were called — the model responded directly and there is
                # no tool evidence to ground-check against. Running the review
                # in this case causes the model to meta-respond asking for a
                # "candidate answer" instead of delivering the actual response.
                await task_handle.record_step(
                    "final_review_skipped",
                    iteration=iteration,
                    details={"reason": "no_tool_evidence"},
                )
            else:
                task_plan = task_plan_state.get("plan")
                step_count = (
                    len(task_plan.steps)
                    if task_plan is not None and task_plan.steps
                    else 0
                )
                if (
                    cfg.final_review_max_plan_steps is not None
                    and task_plan is not None
                    and step_count <= cfg.final_review_max_plan_steps
                ):
                    await task_handle.record_step(
                        "final_review_skipped",
                        iteration=iteration,
                        details={
                            "reason": "plan_step_threshold",
                            "step_count": step_count,
                            "max_plan_steps": cfg.final_review_max_plan_steps,
                        },
                    )
                else:
                    await task_handle.record_step(
                        "final_review",
                        iteration=iteration,
                        details={"steps_to_verify": step_count},
                    )
                    final_response = await self._final_review_pass(
                        messages=messages,
                        candidate_response=final_response,
                        ctx=ctx,
                        base_model_kwargs=base_model_kwargs,
                        reasoning_cfg=reasoning_cfg,
                        task_plan=task_plan_state.get("plan"),
                    )

        # Layer 3 — deterministic faithfulness backstop (5.10).
        # Catches fabricated completion claims for skipped steps that the
        # review model may have failed to remove.
        if final_response:
            _plan_for_check = task_plan_state.get("plan")
            final_response = self._check_plan_faithfulness(
                final_response, _plan_for_check
            )

        # Layer 4 — unconditional non-done footer.
        # Regardless of what the model wrote, always append an honest accounting
        # of every step that was not done so the user is never left with a
        # fabricated success story and no caveat at all.
        _footer_plan = task_plan_state.get("plan")
        if _footer_plan:
            _non_done = [s for s in _footer_plan.steps if s.status != "done"]
            if _non_done:
                _footer_lines = ["\n\n**Steps not completed:**"]
                for _s in _non_done:
                    _label = "skipped" if _s.status == "skipped" else _s.status
                    _reason = _s.skip_reason or "could not be completed"
                    _footer_lines.append(f"- [{_label}] {_s.description}: {_reason}")
                _footer = "\n".join(_footer_lines)
                if "steps not completed" not in final_response.lower():
                    final_response += _footer

        # Grounding verification
        if final_response and result_attributions:
            final_response, unattributed = self._verify_grounding(
                final_response, result_attributions, strict=cfg.strict_grounding
            )
            if unattributed:
                logger.warning(
                    "SkillAction: %d unattributed claims: %s",
                    len(unattributed),
                    unattributed[:5],
                )

        await task_handle.record_step(
            "response",
            iteration=iteration,
            details={
                "length": len(final_response),
                "loop_phase": loop_phase.value,
                "termination_reason": termination_reason.value,
                "preview": final_response[:300],
            },
        )
        await task_handle.update_metadata(
            best_candidate_length=len((best_candidate or "").strip()),
        )

        # Persist evidence log to conversation context
        if cfg.enable_evidence_log and ctx.conversation:
            try:
                evidence_log.persist_to(ctx.conversation)
                await ctx.conversation.save()
            except Exception as e_err:
                logger.warning("SkillAction: evidence log persist failed: %s", e_err)

        # Close remaining open skill envelopes and persist activation log
        _closed_envelopes = tool_executor.close_all_skill_envelopes(
            termination_reason=(
                termination_reason.value
                if hasattr(termination_reason, "value")
                else str(termination_reason)
            )
        )
        if ctx.conversation:
            try:
                context = getattr(ctx.conversation, "context", None)
                if isinstance(context, dict):
                    context["_skill_activation_log"] = [
                        {
                            "skill_name": e.skill_name,
                            "activated_at_iteration": e.activated_at_iteration,
                            "duration_ms": e.duration_ms,
                            "tool_count": e.tool_count,
                            "tool_success_rate": e.tool_success_rate,
                            "total_tool_latency_ms": e.total_tool_latency_ms,
                            "was_completed": e.was_completed,
                            "termination_reason": e.termination_reason,
                            "preflight_warnings": e.preflight_warnings,
                        }
                        for e in tool_executor.skill_envelopes
                    ]
                    await ctx.conversation.save()
            except Exception as e_sk:
                logger.warning(
                    "SkillAction: skill activation log persist failed: %s", e_sk
                )

        # Clear checkpoint on clean exit
        if cfg.enable_checkpoints:
            await checkpoint_store.clear()

        # Tally abandoned (pending/in_progress) and intentionally skipped steps.
        _final_plan = task_plan_state.get("plan")
        _abandoned_steps = (
            len(_final_plan.pending_steps()) if _final_plan is not None else 0
        )
        _intentional_skips = (
            len(_final_plan.skipped_steps()) if _final_plan is not None else 0
        )

        return SkillRunResult(
            final_response=final_response,
            termination_reason=termination_reason,
            stuck_corrections=stuck_detector.corrections,
            result_attributions=result_attributions,
            iterations=iteration,
            duration_seconds=time.monotonic() - loop_start,
            task_id=getattr(task_handle, "task_id", None),
            activated_skills=sorted(tool_executor.activated_skills),
            task_plan_abandoned_steps=_abandoned_steps,
            task_plan_intentional_skips=_intentional_skips,
            metadata={
                "skill_activation": tool_executor.skill_activation_aggregates(),
                "tool_envelope_count": len(tool_executor.envelopes),
                "tool_success_rate": tool_executor.success_rate(),
            },
        )

    # ---------------------------------------------------------------------------
    # Output delivery
    # ---------------------------------------------------------------------------

    async def _emit(
        self,
        *,
        ctx: SkillRunContext,
        content: str,
        category: str = "user",
        thought_type: Optional[str] = None,
        segment_id: Optional[str] = None,
        streaming_complete: bool = True,
        relay_to_adapters: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        allow_empty: bool = False,
    ) -> None:
        """Emit content via ResponseBus or the caller's publish_callback."""
        if not content and not allow_empty:
            return
        if ctx.publish_callback:
            try:
                await ctx.publish_callback(
                    content,
                    category=category,
                    thought_type=thought_type,
                    segment_id=segment_id,
                    streaming_complete=streaming_complete,
                    relay_to_adapters=relay_to_adapters,
                )
            except Exception as cb_err:
                logger.warning("SkillAction: publish_callback failed: %s", cb_err)
            return

        if not (ctx.response_bus and ctx.session_id and ctx.interaction):
            return

        try:
            interaction_id = getattr(ctx.interaction, "id", None)
            user_id = getattr(ctx.interaction, "user_id", None)
            await ctx.response_bus.publish(
                session_id=ctx.session_id,
                content=content,
                channel=ctx.channel,
                stream=ctx.stream,
                interaction_id=interaction_id,
                interaction=ctx.interaction,
                user_id=user_id,
                metadata=metadata or {},
                streaming_complete=streaming_complete,
                transient=(category == "thought"),
                category=category,
                thought_type=thought_type,
                segment_id=segment_id,
                relay_to_adapters=relay_to_adapters,
            )
        except Exception as pub_err:
            logger.warning("SkillAction: response_bus.publish failed: %s", pub_err)

    async def _emit_task_status(self, *, ctx: SkillRunContext, message: str) -> None:
        """Emit a concise user-visible status update."""
        if not ctx.config.stream_tool_progress:
            return

        await self._emit(
            ctx=ctx,
            content=message,
            category="user",
            streaming_complete=True,
            relay_to_adapters=False,
        )

    # ---------------------------------------------------------------------------
    # Model calls
    # ---------------------------------------------------------------------------

    async def _call_model(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        ctx: SkillRunContext,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
        *,
        profile: str = "reasoning",
        loop_iteration: Optional[int] = None,
    ) -> Any:
        """Invoke the model action with current messages and tools."""
        model_action = ctx.model_action
        profile_cfg = self._reasoning_cfg_for_profile(reasoning_cfg, profile)
        translated = model_action.translate_reasoning_config(profile_cfg)
        model_kwargs = {**base_model_kwargs, **translated}
        final_messages = model_action.prepare_messages_for_reasoning(messages)

        common_kw: Dict[str, Any] = dict(
            messages=final_messages,
            system=final_messages[0].get("content") if final_messages else None,
            tools=tools if tools else None,
            calling_action_name="SkillAction",
            prompt_for_observability=ctx.utterance,
            **model_kwargs,
        )

        cfg = ctx.config
        if loop_iteration is None:
            return await model_action.query_messages(stream=False, **common_kw)

        model_result = await model_action.query_messages(stream=True, **common_kw)

        mirror_assistant = model_action.should_mirror_assistant_stream_as_thoughts(
            profile_cfg, **model_kwargs
        )
        should_publish = cfg.stream_thinking or cfg.stream_reasoning
        if mirror_assistant and should_publish:
            mirror_assistant = False

        segment_id = f"iter-{loop_iteration}-reasoning"

        async def drain_text() -> None:
            if mirror_assistant:
                async for chunk in model_result.iter_stream():
                    if chunk:
                        await self._emit(
                            ctx=ctx,
                            content=chunk,
                            category="thought",
                            thought_type="reasoning",
                            segment_id=segment_id,
                            streaming_complete=False,
                            relay_to_adapters=cfg.relay_thoughts_to_channels,
                        )
                await self._emit(
                    ctx=ctx,
                    content="",
                    category="thought",
                    thought_type="reasoning",
                    segment_id=segment_id,
                    streaming_complete=True,
                    relay_to_adapters=cfg.relay_thoughts_to_channels,
                    allow_empty=True,
                )
            else:
                await model_result.get_response()

        async def drain_or_publish_thinking() -> None:
            if not should_publish:
                async for _ in model_result.iter_thinking():
                    pass
                return
            had_delta = False
            async for delta in model_result.iter_thinking():
                if not had_delta and not mirror_assistant:
                    await self._emit(
                        ctx=ctx,
                        content="Thinking through this...",
                        category="thought",
                        thought_type="reasoning",
                        segment_id=segment_id,
                        streaming_complete=False,
                        relay_to_adapters=cfg.relay_thoughts_to_channels,
                    )
                had_delta = True
                await self._emit(
                    ctx=ctx,
                    content=delta,
                    category="thought",
                    thought_type="reasoning",
                    segment_id=segment_id,
                    streaming_complete=False,
                    relay_to_adapters=cfg.relay_thoughts_to_channels,
                )
            if had_delta and not mirror_assistant:
                await self._emit(
                    ctx=ctx,
                    content="",
                    category="thought",
                    thought_type="reasoning",
                    segment_id=segment_id,
                    streaming_complete=True,
                    relay_to_adapters=cfg.relay_thoughts_to_channels,
                    allow_empty=True,
                )

        await asyncio.gather(drain_text(), drain_or_publish_thinking())
        return model_result

    async def _force_termination(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        ctx: SkillRunContext,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
        checklist: Optional[List[Dict[str, str]]] = None,
        *,
        termination_cause: str = "iter_cap",
    ) -> str:
        if checklist:
            lines = []
            for c in checklist:
                status = c.get("status", "pending")
                item = c.get("item", "unknown")
                entry = f"- [{status}] {item}"
                skip_reason = c.get("skip_reason")
                if skip_reason:
                    entry += f" (reason: {skip_reason})"
                lines.append(entry)
            checklist_text = "\n".join(lines)
            checklist_section = (
                "COMPLETION CHECKLIST (you MUST address each item):\n"
                + checklist_text
                + "\n\nFor each checklist item:\n"
                "- If you have tool-confirmed evidence, summarize it with attribution.\n"
                "- If you do NOT have evidence for an item, explicitly state: "
                '"I was unable to verify [item] because [reason]."\n'
                "- For items marked [skipped], include the recorded skip reason.\n"
                "- Do NOT fabricate evidence for incomplete items.\n"
            )
            # Use cause-specific template when available, else generic
            cause_templates = {
                "iter_cap": FORCED_TERMINATION_PROMPT_ITER_CAP,
                "time_cap": FORCED_TERMINATION_PROMPT_TIME_CAP,
                "stuck": FORCED_TERMINATION_PROMPT_STUCK,
            }
            template = cause_templates.get(
                termination_cause, FORCED_TERMINATION_PROMPT_TEMPLATE
            )
            if termination_cause in cause_templates:
                prompt = template.format(checklist_section=checklist_section)
            else:
                prompt = template.format(checklist=checklist_text)
        else:
            prompt = FORCED_TERMINATION_PROMPT_NO_CHECKLIST
        messages.append({"role": "user", "content": prompt})
        try:
            result = await self._call_model(
                messages, None, ctx, base_model_kwargs, reasoning_cfg, profile="final"
            )
            return await result.get_response() or result.response or ""
        except Exception as e:
            logger.error("SkillAction: forced termination call failed: %s", e)
            return "I was unable to complete the task within the allowed steps."

    # ------------------------------------------------------------------
    # Loop-iteration helpers (extracted from _run_loop for readability)
    # ------------------------------------------------------------------

    async def _handle_no_tool_response(
        self,
        *,
        model_result: Any,
        messages: List[Dict[str, Any]],
        cfg: "SkillRunConfig",
        ctx: "SkillRunContext",
        task_plan_state: Dict[str, Any],
        task_handle: Any,
        tool_executor: "ToolExecutor",
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: Any,
        discovered_skills: Any,
        iteration: int,
        loop_phase: "LoopPhase",
        termination_reason: "TerminationReason",
        final_response: str,
        candidate_response: Optional[str],
        best_candidate: Optional[str],
        task_nudges: int,
        task_nudges_total: int,
        skill_first_retries: int,
        retry_nudges: int,
        tools_ever_called: Set[str],
        nontrivial_tools_ever_called: Set[str],
    ) -> "_NoToolResult":
        """Process a model turn that produced no tool calls.

        Handles candidate response collection, skill-first nudging, task-plan
        nudging, and final candidate acceptance.  Returns a :class:`_NoToolResult`
        indicating how the loop should proceed.
        """
        loop_phase = LoopPhase.OBSERVE
        candidate_response = await model_result.get_response()
        if not candidate_response and model_result.response:
            candidate_response = model_result.response

        best_candidate = self._update_best_candidate(best_candidate, candidate_response)
        await task_handle.record_step(
            "candidate",
            iteration=iteration,
            details={
                "length": len((candidate_response or "").strip()),
                "preview": (candidate_response or "")[:200],
            },
        )

        # Skill-first nudge
        if self._should_retry_for_skill_first(
            cfg=cfg,
            discovered_skills=discovered_skills,
            tool_executor=tool_executor,
            utterance=ctx.utterance or "",
            retries=skill_first_retries,
            candidate_response=candidate_response,
            tools_ever_called=tools_ever_called,
            nontrivial_tools_called=nontrivial_tools_ever_called,
        ):
            loop_phase = LoopPhase.NUDGE
            await task_handle.record_step(
                "skill_first_retry",
                iteration=iteration,
                details={"nudge_index": skill_first_retries + 1},
            )
            retry_nudges += 1
            await task_handle.update_metadata(retry_nudges_fired=retry_nudges)
            messages.append({"role": "assistant", "content": candidate_response or ""})
            messages.append({"role": "user", "content": SKILL_FIRST_RETRY_PROMPT})
            skill_first_retries += 1
            return _NoToolResult(
                loop_phase=loop_phase,
                termination_reason=termination_reason,
                final_response=final_response,
                candidate_response=candidate_response,
                best_candidate=best_candidate,
                task_nudges=task_nudges,
                task_nudges_total=task_nudges_total,
                skill_first_retries=skill_first_retries,
                retry_nudges=retry_nudges,
                control="continue",
            )

        # Pending-step gate: task-plan state is the source of truth.
        task_plan = task_plan_state["plan"]
        if task_plan is not None and task_plan.has_pending_steps():
            consecutive_limit = cfg.task_nudge_retry_limit
            total_limit = cfg.max_total_task_nudges
            nudge_allowed = (
                task_nudges < consecutive_limit and task_nudges_total < total_limit
            )
            if nudge_allowed:
                loop_phase = LoopPhase.NUDGE
                is_final_nudge = (
                    task_nudges == consecutive_limit - 1
                    or task_nudges_total == total_limit - 1
                )
                nudge_prompt = (
                    PENDING_STEPS_NUDGE_PROMPT_FINAL
                    if is_final_nudge
                    else PENDING_STEPS_NUDGE_PROMPT
                )
                await task_handle.record_step(
                    "task_plan_nudge",
                    iteration=iteration,
                    details={
                        "nudge_index": task_nudges + 1,
                        "is_final_nudge": is_final_nudge,
                        "pending_steps": task_plan.format_for_model(),
                    },
                )
                retry_nudges += 1
                await task_handle.update_metadata(retry_nudges_fired=retry_nudges)
                messages.append(
                    {"role": "assistant", "content": candidate_response or ""}
                )
                messages.append(
                    {
                        "role": "user",
                        "content": nudge_prompt.format(
                            pending=task_plan.format_for_model()
                        ),
                    }
                )
                task_nudges += 1
                task_nudges_total += 1
                return _NoToolResult(
                    loop_phase=loop_phase,
                    termination_reason=termination_reason,
                    final_response=final_response,
                    candidate_response=candidate_response,
                    best_candidate=best_candidate,
                    task_nudges=task_nudges,
                    task_nudges_total=task_nudges_total,
                    skill_first_retries=skill_first_retries,
                    retry_nudges=retry_nudges,
                    control="continue",
                )
            else:
                # Nudge limit exhausted — escalate to forced termination.
                pending_count = len(task_plan.pending_steps())
                logger.warning(
                    "SkillAction: nudge limit exhausted with %d pending step(s); "
                    "escalating to forced termination",
                    pending_count,
                )
                await task_handle.update_metadata(
                    task_plan_incomplete_accepted=True,
                    task_plan_pending_at_termination=task_plan.format_for_model(),
                )
                await task_handle.record_step(
                    "task_plan_incomplete_forced",
                    iteration=iteration,
                    details={
                        "pending_count": pending_count,
                        "pending_steps": task_plan.format_for_model(),
                    },
                )
                messages.append(
                    {"role": "assistant", "content": candidate_response or ""}
                )
                final_response = await self._force_termination(
                    messages,
                    tool_executor.get_tools_list(),
                    ctx,
                    base_model_kwargs,
                    reasoning_cfg,
                    checklist=self._task_plan_pending_checklist(task_plan),
                    termination_cause="stuck",
                )
                termination_reason = TerminationReason.STUCK
                loop_phase = LoopPhase.TERMINATE
                return _NoToolResult(
                    loop_phase=loop_phase,
                    termination_reason=termination_reason,
                    final_response=final_response,
                    candidate_response=candidate_response,
                    best_candidate=best_candidate,
                    task_nudges=task_nudges,
                    task_nudges_total=task_nudges_total,
                    skill_first_retries=skill_first_retries,
                    retry_nudges=retry_nudges,
                    control="break",
                )

        # Accept candidate
        chosen = candidate_response or ""
        if self._should_prefer_best_over_candidate(cfg, chosen, best_candidate):
            await task_handle.record_step(
                "candidate_discarded",
                iteration=iteration,
                details={"reason": "degenerate_or_shrunk_vs_best"},
            )
            final_response = (best_candidate or chosen) or ""
        else:
            final_response = chosen
            await task_handle.record_step(
                "candidate_accepted",
                iteration=iteration,
                details={"length": len(final_response.strip())},
            )
        termination_reason = TerminationReason.COMPLETED
        loop_phase = LoopPhase.TERMINATE
        return _NoToolResult(
            loop_phase=loop_phase,
            termination_reason=termination_reason,
            final_response=final_response,
            candidate_response=candidate_response,
            best_candidate=best_candidate,
            task_nudges=task_nudges,
            task_nudges_total=task_nudges_total,
            skill_first_retries=skill_first_retries,
            retry_nudges=retry_nudges,
            control="break",
        )

    async def _handle_tool_calls(
        self,
        *,
        model_result: Any,
        messages: List[Dict[str, Any]],
        cfg: "SkillRunConfig",
        ctx: "SkillRunContext",
        task_plan_state: Dict[str, Any],
        task_handle: Any,
        tool_executor: "ToolExecutor",
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: Any,
        loop_start: float,
        iteration: int,
        is_meta_utterance: bool,
        stuck_detector: Any,
        checkpoint_store: Any,
        evidence_log: Any,
        compactor: Any,
        result_attributions: List[Dict[str, Any]],
        loop_phase: "LoopPhase",
        termination_reason: "TerminationReason",
        final_response: str,
        task_nudges: int,
        tools_ever_called: Set[str],
        nontrivial_tools_ever_called: Set[str],
    ) -> "_ToolCallResult":
        """Dispatch tool calls and collect results for one loop iteration.

        Handles plan-first gating, stuck detection, evidence logging,
        attribution extraction, result streaming, and context compaction.
        Returns a :class:`_ToolCallResult` indicating how the loop should
        proceed.
        """
        tool_calls = model_result.tool_calls
        reordered = self._reorder_task_calls_dependency_first(tool_calls)
        to_dispatch, synthetic_plan_blocks, plan_first_blocked_names = (
            self._apply_plan_first_tool_gate(
                reordered,
                plan_first=cfg.plan_first,
                has_task_plan=task_plan_state.get("plan") is not None,
                is_meta_utterance=is_meta_utterance,
                activated_skill_names=set(
                    getattr(tool_executor, "activated_skills", set()) or ()
                ),
            )
        )
        if plan_first_blocked_names:
            await task_handle.record_step(
                "plan_first_gated",
                iteration=iteration,
                details={"blocked_tools": sorted(plan_first_blocked_names)},
            )
        stuck_result = stuck_detector.record(reordered)
        loop_phase = LoopPhase.TOOL_DISPATCH

        # Per-skill budget enforcement: gate tools whose skill has exceeded its
        # iteration or time budget (P0-4). Integrates with the existing
        # to_dispatch / synthetic pattern.
        skill_budget_blocked_names: Set[str] = set()
        if cfg.max_iterations_per_skill > 0 or cfg.max_duration_per_skill_seconds > 0:
            _revised_dispatch: List[Dict[str, Any]] = []
            _revised_synthetic: List[Dict[str, Any]] = list(synthetic_plan_blocks)
            for tc in to_dispatch:
                fn = tc.get("function") or {}
                name = (fn.get("name") or "unknown") or "unknown"
                skill_prefix = SkillAction._namespaced_skill_tool_prefix(name)
                if skill_prefix and skill_prefix in getattr(
                    tool_executor, "activated_skills", set()
                ):
                    budget_error = tool_executor.check_skill_budget_exhausted(
                        skill_name=skill_prefix,
                        max_iterations=cfg.max_iterations_per_skill,
                        max_duration_seconds=cfg.max_duration_per_skill_seconds,
                    )
                    if budget_error:
                        skill_budget_blocked_names.add(name)
                        tid = str(tc.get("id") or "")
                        _revised_synthetic.append(
                            {
                                "role": "tool",
                                "tool_call_id": tid,
                                "content": f"Error: {budget_error}",
                            }
                        )
                        continue
                _revised_dispatch.append(tc)
            to_dispatch = _revised_dispatch
            synthetic_plan_blocks = _revised_synthetic

        if skill_budget_blocked_names:
            await task_handle.record_step(
                "skill_budget_gated",
                iteration=iteration,
                details={"blocked_tools": sorted(skill_budget_blocked_names)},
            )

        tool_names = [tc.get("function", {}).get("name", "unknown") for tc in reordered]
        for n in tool_names:
            if n and n != "unknown":
                tools_ever_called.add(n)
                if n in plan_first_blocked_names:
                    continue
                if not (n in _SKILL_HELPER_TOOL_NAMES or n.startswith("skill_hub__")):
                    nontrivial_tools_ever_called.add(n)

        helper_snapshot = sorted(
            t
            for t in tools_ever_called
            if t in _SKILL_HELPER_TOOL_NAMES or t.startswith("skill_hub__")
        )
        if helper_snapshot:
            await task_handle.update_metadata(helper_tools_called=helper_snapshot)

        # Tool call announce
        intermediate_text = (model_result.response or "").strip()
        if cfg.commit_intermediate_messages and intermediate_text:
            await self._emit(
                ctx=ctx,
                content=intermediate_text,
                category="thought",
                thought_type="reasoning",
                segment_id=None,
                streaming_complete=True,
                relay_to_adapters=False,
                metadata={"iteration": iteration, "intermediate": True},
            )

        if cfg.stream_tool_progress:
            for idx, tc in enumerate(reordered):
                tool_name = tc.get("function", {}).get("name", "unknown")
                display_name = self._clean_tool_name(tool_name)
                intent = self._extract_tool_intent(
                    tc.get("function", {}).get("arguments", "")
                )
                opener = MONOLOGUE_OPENERS[(iteration + idx) % len(MONOLOGUE_OPENERS)]
                await self._emit(
                    ctx=ctx,
                    content=TOOL_CALL_ANNOUNCE_TEMPLATE.format(
                        opener=opener, tool_name=display_name, intent=intent
                    ),
                    category="thought",
                    thought_type="tool_call",
                    segment_id=f"iter-{iteration}-call-{tool_name}-{idx}",
                    streaming_complete=True,
                    relay_to_adapters=cfg.relay_thoughts_to_channels,
                )

        assistant_msg = LoopContext.build_assistant_content(model_result)
        messages.append(assistant_msg)

        # Checkpoint before tool dispatch
        if cfg.enable_checkpoints:
            ckpt = LoopCheckpoint(
                iteration=iteration,
                phase=LoopPhase.TOOL_DISPATCH.value,
                elapsed_seconds=time.monotonic() - loop_start,
                pending_tool_names=tool_names,
                termination_reason_candidate=termination_reason.value,
            )
            await checkpoint_store.save(ckpt)

        tool_start = time.monotonic()
        dispatch_results: List[Dict[str, Any]] = []
        if to_dispatch:
            dispatch_results = await tool_executor.dispatch(to_dispatch)
        tool_result_messages = self._merge_tool_dispatch_with_synthetic(
            reordered, dispatch_results, synthetic_plan_blocks
        )
        tool_duration_ms = int((time.monotonic() - tool_start) * 1000)

        # Per-skill iteration tracking: increment counter for each dispatched tool
        # belonging to an activated skill.
        if cfg.max_iterations_per_skill > 0 or cfg.max_duration_per_skill_seconds > 0:
            for tc in to_dispatch:
                fn = tc.get("function") or {}
                name = (fn.get("name") or "unknown") or "unknown"
                skill_prefix = SkillAction._namespaced_skill_tool_prefix(name)
                if skill_prefix:
                    tool_executor.record_skill_iteration(skill_prefix)

        # Record raw evidence
        if cfg.enable_evidence_log:
            for tr_msg, tc in zip(tool_result_messages, reordered):
                evidence_log.append(
                    iteration=iteration,
                    tool_call_id=tr_msg.get("tool_call_id", ""),
                    tool_name=tc.get("function", {}).get("name", "unknown"),
                    input_args=tc.get("function", {}).get("arguments", ""),
                    content=tr_msg.get("content", ""),
                )

        # Attribution extraction
        tool_call_id_to_name: Dict[str, str] = {
            tc.get("id", ""): tc.get("function", {}).get("name", "unknown")
            for tc in reordered
        }
        for tr_msg in tool_result_messages:
            content = tr_msg.get("content", "")
            tool_call_id = tr_msg.get("tool_call_id", "")
            if content and not content.startswith("Error:"):
                result_attributions.extend(
                    self._extract_result_attributions(content, tool_call_id)
                )

        # Tool result announce
        if cfg.stream_tool_progress:
            for idx, tr_msg in enumerate(tool_result_messages):
                content = tr_msg.get("content", "")
                tool_call_id = tr_msg.get("tool_call_id", "")
                tool_name = tool_call_id_to_name.get(tool_call_id, "unknown")
                display_name = self._clean_tool_name(tool_name)
                is_error = content.startswith("Error:")
                if is_error:
                    error_detail = content[len("Error:") :].strip()
                    err_tpl = MONOLOGUE_RESULT_ERR[
                        (iteration + idx) % len(MONOLOGUE_RESULT_ERR)
                    ]
                    announcement = ERROR_ANNOUNCE_TEMPLATE.format(
                        error_line=err_tpl.format(
                            tool_name=display_name,
                            error=error_detail[:120] or "unknown error",
                        )
                    )
                else:
                    preview = self._format_result_preview(content)
                    ok_tpl = MONOLOGUE_RESULT_OK[
                        (iteration + idx) % len(MONOLOGUE_RESULT_OK)
                    ]
                    announcement = TOOL_RESULT_ANNOUNCE_TEMPLATE.format(
                        result_line=ok_tpl.format(
                            tool_name=display_name, preview=preview
                        )
                    )
                await self._emit(
                    ctx=ctx,
                    content=announcement,
                    category="thought",
                    thought_type="tool_result",
                    segment_id=f"iter-{iteration}-result-{tool_call_id or 'unknown'}",
                    streaming_complete=True,
                    relay_to_adapters=cfg.relay_thoughts_to_channels,
                )

        # Accumulate tool results
        result_statuses = [
            {
                "tool_call_id": tr.get("tool_call_id", ""),
                "is_error": tr.get("is_error", False),
                "content_preview": (tr.get("content", "") or "")[:200],
            }
            for tr in tool_result_messages
        ]
        await task_handle.record_step(
            "tool_result",
            iteration=iteration,
            details={
                "duration_ms": tool_duration_ms,
                "count": len(tool_result_messages),
                "results": result_statuses,
                "attributions_added": len(result_attributions),
                "tools": tool_names,
            },
        )

        # Stuck detection
        if stuck_result:
            if stuck_result == "FORCE_TERMINATE":
                loop_phase = LoopPhase.TERMINATE
                final_response = await self._force_termination(
                    messages,
                    tool_executor.get_tools_list(),
                    ctx,
                    base_model_kwargs,
                    reasoning_cfg,
                    checklist=self._task_plan_pending_checklist(
                        task_plan_state["plan"]
                    ),
                    termination_cause="stuck",
                )
                termination_reason = TerminationReason.STUCK
                messages.extend(tool_result_messages)
                return _ToolCallResult(
                    loop_phase=loop_phase,
                    termination_reason=termination_reason,
                    final_response=final_response,
                    messages=messages,
                    task_nudges=task_nudges,
                    result_attributions=result_attributions,
                    control="break",
                )
            else:
                messages.append({"role": "user", "content": stuck_result})

        messages.extend(tool_result_messages)

        # Reset the consecutive nudge counter only when a plan advance occurred (5.5).
        if "task_tracker" in tool_names and task_plan_state.get(
            "_nudge_reset_requested"
        ):
            task_nudges = 0
            task_plan_state["_nudge_reset_requested"] = False

        if any(n not in _SKILL_HELPER_TOOL_NAMES for n in tool_names):
            task_plan_state["tool_calls_since_complete"] = task_plan_state.get(
                "tool_calls_since_complete", 0
            ) + sum(
                1
                for n in tool_names
                if n not in _SKILL_HELPER_TOOL_NAMES
                and n != "task_tracker"
                and n not in plan_first_blocked_names
            )

        # Evidence-aware compaction
        messages = compactor.compact(messages, evidence_log=evidence_log)

        return _ToolCallResult(
            loop_phase=loop_phase,
            termination_reason=termination_reason,
            final_response=final_response,
            messages=messages,
            task_nudges=task_nudges,
            result_attributions=result_attributions,
            control="",
        )

    async def _final_review_pass(
        self,
        messages: List[Dict[str, Any]],
        candidate_response: str,
        ctx: SkillRunContext,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
        *,
        task_plan: Optional[InLoopTaskPlan] = None,
    ) -> str:
        # Layer 2 — pre-review skipped-step suffix injection.
        # Append a grounded "could not complete" section so the reviewer sees
        # both the candidate claim AND the truth side-by-side.
        skipped_steps = task_plan.skipped_steps() if task_plan else []
        if skipped_steps:
            suffix_lines = ["\n\n**Steps that could not be completed:**"]
            for s in skipped_steps:
                reason = s.skip_reason or "no reason recorded"
                suffix_lines.append(f"- {s.description}: {reason}")
            candidate_response = candidate_response + "\n".join(suffix_lines)

        # Layer 1 — plan-aware review prompt.
        if task_plan and task_plan.steps:
            plan_lines = []
            for s in task_plan.steps:
                entry = f"- step {s.id}: [{s.status}] {s.description}"
                if s.status == "skipped" and s.skip_reason:
                    entry += f" REASON: {s.skip_reason}"
                plan_lines.append(entry)
            plan_summary = "\n".join(plan_lines)
            review_prompt = FINAL_REVIEW_PROMPT_WITH_PLAN.format(
                plan_summary=plan_summary
            )
        else:
            review_prompt = FINAL_REVIEW_PROMPT

        review_msgs = list(messages)
        review_msgs.append({"role": "assistant", "content": candidate_response})
        review_msgs.append({"role": "user", "content": review_prompt})
        try:
            reviewed = await self._call_model(
                review_msgs,
                None,
                ctx,
                base_model_kwargs,
                reasoning_cfg,
                profile="final",
            )
            text = await reviewed.get_response()
            return text or reviewed.response or candidate_response
        except Exception as exc:
            logger.warning("SkillAction: final review pass failed: %s", exc)
            return candidate_response

    # ---------------------------------------------------------------------------
    # Tool and skill helpers
    # ---------------------------------------------------------------------------

    def _make_read_skill_handler(
        self,
        discovered_skills: Dict[str, Any],
        skill_catalog: SkillCatalog,
        tool_executor: ToolExecutor,
        action_resolver: Optional[ActionResolver],
        cfg: SkillRunConfig,
        visitor: Any = None,
    ):
        async def read_skill_handler(args):
            skill_name = args.get("skill_name")
            if skill_name not in discovered_skills:
                return f"Error: Skill '{skill_name}' not found."
            limit_error = skill_catalog.check_activation_limit(
                skill_name=skill_name,
                activated_skills=tool_executor.activated_skills,
                max_activations=cfg.max_skill_activations,
            )
            if limit_error:
                return limit_error
            req_error = await skill_catalog.validate_requirements(
                skill_name=skill_name, action_resolver=action_resolver
            )
            if req_error:
                return req_error
            registered_tools = await tool_executor.activate_skill(
                skill_name, action_resolver=action_resolver, visitor=visitor
            )
            skill_data = discovered_skills[skill_name]
            scope_hint = str(
                skill_data.get("scope_hint")
                or skill_data.get("description")
                or "the workflow described in this skill"
            )
            result_text = READ_SKILL_RESULT_TEMPLATE.format(
                skill_name=skill_name,
                tools=", ".join(registered_tools) if registered_tools else "(none)",
                content=skill_data["content"],
            )
            plan_steps = skill_data.get("plan_steps") or []
            if plan_steps:
                steps_list = "\n".join(
                    f"  {index}. {step}"
                    for index, step in enumerate(plan_steps, start=1)
                )
                result_text += READ_SKILL_PLAN_STEPS_HINT.format(steps_list=steps_list)
            if cfg.strict_grounding:
                result_text += "\n\n" + GROUNDING_INSTRUCTION_TEMPLATE.format(
                    skill_name=skill_name, scope_hint=scope_hint
                )
            return result_text

        return read_skill_handler

    def _register_skill_helper_tools(
        self,
        tool_executor: ToolExecutor,
        skill_catalog: SkillCatalog,
        discovered_skills: Dict[str, Any],
        ctx: SkillRunContext,
    ) -> None:
        tool_executor.register_dynamic_tool(
            name="list_skills",
            tool_def_dict={
                "name": "list_skills",
                "description": LIST_SKILLS_TOOL_DESCRIPTION,
                "parameters": {"type": "object", "properties": {}},
            },
            handler=lambda args: skill_catalog.render_catalog(),
        )

        async def skill_search_handler(args):
            query = str(args.get("query", "")).strip()
            top_k = max(1, int(args.get("top_k", 5)))
            mode = str(args.get("mode", "hybrid")).strip().lower()
            if mode not in ("lexical", "semantic", "hybrid"):
                mode = "hybrid"
            catalog = skill_catalog
            if mode == "lexical":
                return catalog.search(query, top_k=top_k)
            if mode in ("semantic", "hybrid") and ctx.config.semantic_skill_search:
                try:
                    return await catalog.search_semantic(
                        query,
                        top_k=top_k,
                        model_action=ctx.model_action,
                        base_model_kwargs={
                            "model": ctx.config.model,
                            "temperature": 0.1,
                            "max_tokens": 500,
                        },
                    )
                except Exception:
                    logger.warning(
                        "Semantic skill search failed, falling back to lexical",
                        exc_info=True,
                    )
            return catalog.search(query, top_k=top_k)

        tool_executor.register_dynamic_tool(
            name="skill_search",
            tool_def_dict={
                "name": "skill_search",
                "description": SKILL_SEARCH_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                        "mode": {
                            "type": "string",
                            "enum": ["lexical", "semantic", "hybrid"],
                            "default": "hybrid",
                            "description": (
                                "lexical: token overlap only; semantic: LLM re-rank "
                                "(requires semantic_skill_search config); hybrid: default."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
            handler=skill_search_handler,
        )

        async def plan_skills_handler(args):
            query = str(args.get("query", "")).strip()
            top_k = max(1, int(args.get("top_k", 5)))
            mode = str(args.get("mode", "hybrid")).strip().lower()
            if mode not in ("lexical", "semantic", "hybrid"):
                mode = "hybrid"
            catalog = skill_catalog
            if mode == "lexical":
                matches = catalog.search(query, top_k=top_k)
            elif mode in ("semantic", "hybrid") and ctx.config.semantic_skill_search:
                try:
                    matches = await catalog.search_semantic(
                        query,
                        top_k=top_k,
                        model_action=ctx.model_action,
                        base_model_kwargs={
                            "model": ctx.config.model,
                            "temperature": 0.1,
                            "max_tokens": 500,
                        },
                    )
                except Exception:
                    logger.warning(
                        "Semantic skill search failed, falling back to lexical",
                        exc_info=True,
                    )
                    matches = catalog.search(query, top_k=top_k)
            else:
                matches = catalog.search(query, top_k=top_k)
            return (
                "Recommended skill activation plan:\n"
                + matches
                + "\n\nActivate skills ONE AT A TIME. "
                "Complete each skill's workflow before moving to the next."
            )

        tool_executor.register_dynamic_tool(
            name="plan_skills",
            tool_def_dict={
                "name": "plan_skills",
                "description": PLAN_SKILLS_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                        "mode": {
                            "type": "string",
                            "enum": ["lexical", "semantic", "hybrid"],
                            "default": "hybrid",
                            "description": (
                                "Same as skill_search: lexical, semantic (if enabled), or hybrid."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
            handler=plan_skills_handler,
        )

        # preview_skill — inspect tool schemas without activating (P3-14)
        async def preview_skill_handler(args):
            skill_name = args.get("skill_name")
            if skill_name not in discovered_skills:
                return f"Error: Skill '{skill_name}' not found."
            skill_data = discovered_skills[skill_name]
            tool_files = skill_data.get("tool_files", [])
            if not tool_files:
                return (
                    f"Skill '{skill_name}' has no tool modules.\n"
                    f"Description: {skill_data.get('description', '')}\n"
                    f"Content: {skill_data.get('content', '')[:500]}"
                )
            # Temporarily import tool files to extract schemas
            lines = [
                f"Skill: {skill_name}",
                f"Description: {skill_data.get('description', '')}",
                f"Tools ({len(tool_files)}):",
            ]
            import importlib.util as _iu
            import sys as _sys
            from pathlib import Path as _Path

            safe_skill_name = skill_name.replace("-", "_")
            package_name = f"jvagent_skill_{safe_skill_name}"
            for tf in tool_files:
                stem = _Path(tf).stem
                mod_name = f"{package_name}.{stem}"
                try:
                    spec = _iu.spec_from_file_location(mod_name, tf)
                    if not spec or not spec.loader:
                        continue
                    mod = _iu.module_from_spec(spec)
                    mod.__package__ = package_name
                    _sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                    get_def = getattr(mod, "get_tool_definition", None)
                    if get_def:
                        td = get_def()
                        if isinstance(td, dict):
                            tname = td.get("function", {}).get("name") or td.get(
                                "name", stem
                            )
                            tdesc = td.get("function", {}).get("description") or td.get(
                                "description", ""
                            )
                            tparams = td.get("function", {}).get(
                                "parameters"
                            ) or td.get("parameters", {})
                            required = (
                                tparams.get("required", [])
                                if isinstance(tparams, dict)
                                else []
                            )
                            props = (
                                tparams.get("properties", {})
                                if isinstance(tparams, dict)
                                else {}
                            )
                            lines.append(
                                f"\n  {tname}: {tdesc}\n"
                                f"  Parameters: {', '.join(props.keys()) if props else '(none)'}\n"
                                f"  Required: {', '.join(required) if required else '(none)'}"
                            )
                except Exception:
                    continue
            return "\n".join(lines)

        tool_executor.register_dynamic_tool(
            name="preview_skill",
            tool_def_dict={
                "name": "preview_skill",
                "description": (
                    "Preview a skill's tool names, descriptions, and parameter "
                    "schemas WITHOUT activating the skill.  Use this to compare "
                    "tool surfaces across candidate skills before committing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to preview.",
                        }
                    },
                    "required": ["skill_name"],
                },
            },
            handler=preview_skill_handler,
        )

    # ---------------------------------------------------------------------------
    # Decision / task-tracker helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _function_arguments_to_dict(args_raw: Any) -> Dict[str, Any]:
        if args_raw is None:
            return {}
        if isinstance(args_raw, dict):
            return args_raw
        s = str(args_raw).strip()
        if not s:
            return {}
        try:
            parsed: Any = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _read_skill_target_names_from_batch(
        tool_calls: List[Dict[str, Any]]
    ) -> Set[str]:
        names: Set[str] = set()
        for tc in tool_calls:
            fn = tc.get("function") or {}
            if (fn.get("name") or "").strip() != "read_skill":
                continue
            args = SkillAction._function_arguments_to_dict(fn.get("arguments"))
            raw = args.get("skill_name")
            if raw is not None and str(raw).strip():
                names.add(str(raw).strip())
        return names

    @staticmethod
    def _namespaced_skill_tool_prefix(tool_name: str) -> Optional[str]:
        name = (tool_name or "").strip()
        if "__" not in name:
            return None
        prefix, _rest = name.split("__", 1)
        return prefix.strip() if prefix.strip() else None

    @staticmethod
    def _tool_calls_include_task_tracker_create(
        tool_calls: List[Dict[str, Any]]
    ) -> bool:
        for tc in tool_calls:
            fn = tc.get("function") or {}
            if (fn.get("name") or "") != "task_tracker":
                continue
            args = SkillAction._function_arguments_to_dict(fn.get("arguments"))
            if str(args.get("action", "")).strip().lower() == "create":
                return True
        return False

    @staticmethod
    def _reorder_task_calls_dependency_first(
        tool_calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Order tool batch for safe execution: task_tracker create, then read_skill, then rest.

        Creating a plan first lets later tools see an active task. Running ``read_skill``
        before that skill's namespaced tools (e.g. ``answer__search``) ensures activation
        has completed when those tools run (dispatch may be concurrent).
        """
        if not tool_calls:
            return []
        creates: List[Dict[str, Any]] = []
        read_skills: List[Dict[str, Any]] = []
        rest: List[Dict[str, Any]] = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            if name == "task_tracker":
                args = SkillAction._function_arguments_to_dict(fn.get("arguments"))
                if str(args.get("action", "")).strip().lower() == "create":
                    creates.append(tc)
                    continue
            if name == "read_skill":
                read_skills.append(tc)
                continue
            rest.append(tc)
        if not creates and not read_skills:
            return list(tool_calls)
        return creates + read_skills + rest

    @staticmethod
    def _is_plan_exempt_helper_tool_name(name: str) -> bool:
        n = (name or "").strip()
        if not n or n == "unknown":
            return False
        if n in _SKILL_HELPER_TOOL_NAMES or n.startswith("skill_hub__"):
            return True
        return False

    @staticmethod
    def _apply_plan_first_tool_gate(
        tool_calls: List[Dict[str, Any]],
        *,
        plan_first: bool,
        has_task_plan: bool,
        is_meta_utterance: bool,
        activated_skill_names: Set[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Set[str]]:
        """Enforce a task plan before substantive tools when :attr:`plan_first` is on.

        Returns:
            (calls_to_dispatch, synthetic_error_tool_results, blocked_tool_names).
        """
        if not tool_calls:
            return ([], [], set())
        if not plan_first or has_task_plan or is_meta_utterance:
            return (list(tool_calls), [], set())
        if SkillAction._tool_calls_include_task_tracker_create(tool_calls):
            return (list(tool_calls), [], set())

        read_targets = SkillAction._read_skill_target_names_from_batch(tool_calls)
        read_targets_normalized = {t.replace("-", "_") for t in read_targets}

        to_dispatch: List[Dict[str, Any]] = []
        synthetic: List[Dict[str, Any]] = []
        blocked: Set[str] = set()
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "unknown") or "unknown"
            if SkillAction._is_plan_exempt_helper_tool_name(name):
                to_dispatch.append(tc)
                continue
            skill_prefix = SkillAction._namespaced_skill_tool_prefix(name)
            if skill_prefix and (
                skill_prefix in activated_skill_names
                or skill_prefix in read_targets_normalized
            ):
                to_dispatch.append(tc)
                continue
            tid = str(tc.get("id") or "")
            blocked.add(name)
            synthetic.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": PLAN_FIRST_BLOCKED_TOOL_MESSAGE,
                }
            )
        if blocked:
            logger.info(
                "SkillAction: plan_first gate held back %d substantive tool(s) "
                "until task_tracker create: %s",
                len(synthetic),
                sorted(blocked),
            )
        return (to_dispatch, synthetic, blocked)

    @staticmethod
    def _merge_tool_dispatch_with_synthetic(
        tool_calls: List[Dict[str, Any]],
        dispatch_results: List[Dict[str, Any]],
        synthetic_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Assemble per-call tool messages in the same order as *tool_calls*."""
        by_id: Dict[str, Dict[str, Any]] = {}
        for r in dispatch_results + synthetic_results:
            tid = str(r.get("tool_call_id") or "")
            if tid:
                by_id[tid] = r
        out: List[Dict[str, Any]] = []
        for tc in tool_calls:
            tid = str(tc.get("id") or "")
            if tid in by_id:
                out.append(by_id[tid])
        return out

    def _should_retry_for_skill_first(
        self,
        *,
        cfg: SkillRunConfig,
        discovered_skills: Optional[Dict[str, Any]],
        tool_executor: ToolExecutor,
        utterance: str,
        retries: int,
        candidate_response: Optional[str],
        tools_ever_called: Optional[Set[str]],
        nontrivial_tools_called: Optional[Set[str]],
    ) -> bool:
        if not cfg.prioritize_skills_first:
            return False
        if not discovered_skills or not tool_executor:
            return False
        if tool_executor.activated_skills:
            return False
        if retries >= cfg.skill_first_retry_limit:
            return False
        if nontrivial_tools_called:
            return False
        # When nontrivial_tools_called is None (legacy callers don't supply it), fall back to
        # blocking on any tool call; when it is an explicit empty set, allow helper-only calls
        # (list_skills, skill_search, etc.) through.
        if nontrivial_tools_called is None and tools_ever_called:
            return False
        extra = list(cfg.meta_intent_patterns or [])
        if cfg.meta_intent_skip_nudge and SkillCatalog.is_meta_intent(
            utterance, extra or None
        ):
            return False
        # Compute relevance score and run the smalltalk/conversational check
        # BEFORE the retries==0 shortcut so that pure greetings and small-talk
        # are never nudged toward skill discovery, regardless of whether a
        # skill-discovery tool has been called yet.
        catalog = SkillCatalog(discovered_skills)
        score = catalog.top_relevance_score(utterance or "")
        if candidate_response and self._skill_first_utterance_suggests_smalltalk(
            cfg, utterance, candidate_response, score
        ):
            return False
        # Require at least one explicit local skill-discovery attempt before
        # accepting a direct no-tool answer when no skill has been activated yet.
        if retries == 0 and not self._has_attempted_skill_discovery(tools_ever_called):
            return True
        if candidate_response and self._candidate_mentions_discovered_skills(
            candidate_response, discovered_skills
        ):
            if (
                len((candidate_response or "").strip())
                >= cfg.conversational_min_response_chars
            ):
                return False
        return score >= cfg.skill_first_retry_min_relevance

    @staticmethod
    def _has_attempted_skill_discovery(
        tools_ever_called: Optional[Set[str]],
    ) -> bool:
        if not tools_ever_called:
            return False
        discovery_tools = {
            "list_skills",
            "skill_search",
            "plan_skills",
            "read_skill",
            "preview_skill",
        }
        return any(tool in discovery_tools for tool in tools_ever_called)

    def _make_task_tracker_handler(
        self,
        *,
        ctx: SkillRunContext,
        cfg: SkillRunConfig,
        task_plan_state: Dict[str, Any],
        task_handle: Any,
        iteration_getter: Any,
        review_enabled: bool,
    ):
        async def task_tracker_handler(args: Dict[str, Any]) -> str:
            action = str(args.get("action", "")).strip().lower()
            iteration = int(iteration_getter() or 0)

            if action == "create":
                raw_steps = args.get("steps")
                if not isinstance(raw_steps, list):
                    return "Error: `steps` must be an array of step descriptions."

                steps = [str(step).strip() for step in raw_steps if str(step).strip()]
                if not steps:
                    return "Error: `steps` must contain at least one non-empty step."

                # 5.8a — Validate step specificity: reject trivially-vague "dummy" plans
                # that would bypass the plan-first gate without describing actual work.
                _vague_warnings = self._check_step_specificity(steps)
                if _vague_warnings:
                    logger.warning(
                        "SkillAction: task plan has %d vague step(s): %s",
                        len(_vague_warnings),
                        _vague_warnings,
                    )

                # 5.8 — Enforce step count ceiling and warn when plan exceeds budget.
                if len(steps) > cfg.max_task_plan_steps:
                    return (
                        f"Error: plan has {len(steps)} steps which exceeds the "
                        f"maximum of {cfg.max_task_plan_steps}. Split the task into "
                        "smaller phases or reduce the number of steps."
                    )
                remaining_iterations = cfg.max_iterations - iteration
                if len(steps) > remaining_iterations:
                    logger.warning(
                        "SkillAction: task plan has %d steps but only %d iterations remain; "
                        "plan may not complete within the iteration budget.",
                        len(steps),
                        remaining_iterations,
                    )

                # 5.1 — Guard against silent plan re-creation mid-execution.
                existing_plan = task_plan_state.get("plan")
                if existing_plan is not None:
                    if existing_plan.has_pending_steps():
                        # Block re-creation while steps are still pending to
                        # prevent the model from "escaping" nudging by resetting
                        # to a shorter plan.
                        current = existing_plan.current_step()
                        return (
                            "Error: a task plan with pending steps already exists. "
                            "Complete or skip the remaining steps before creating a "
                            "new plan. "
                            + (
                                f"Current step: {current.id}: {current.description}"
                                if current
                                else "Use `action=read` to review remaining steps."
                            )
                        )
                    # Plan finished (all done/skipped) — allow revision but log it.
                    await task_handle.record_step(
                        "task_plan_revised",
                        iteration=iteration,
                        details={
                            "old_plan": existing_plan.to_checklist(),
                            "new_steps": steps,
                        },
                    )

                task_plan = InLoopTaskPlan(
                    steps=[
                        TaskStep(id=idx + 1, description=description)
                        for idx, description in enumerate(steps)
                    ],
                    created_at_iteration=iteration,
                )
                task_plan_state["plan"] = task_plan
                # Reset per-step validation counter on new plan creation.
                task_plan_state["tool_calls_since_complete"] = 0
                await task_handle.record_step(
                    "task_plan_created",
                    iteration=iteration,
                    details={"steps": steps},
                )
                await task_handle.update_metadata(
                    task_plan=task_plan.to_checklist(),
                    task_plan_active=task_plan.has_pending_steps(),
                    task_plan_created_iteration=iteration,
                    task_plan_pending_count=len(task_plan.pending_steps()),
                )
                await self._emit_task_status(
                    ctx=ctx,
                    message=STATUS_PLAN_CREATED.format(
                        n=len(task_plan.steps),
                        plural="" if len(task_plan.steps) == 1 else "s",
                        review_suffix=" + review" if review_enabled else "",
                    ),
                )
                plan_response = "Task plan created:\n" + task_plan.format_for_model()
                if _vague_warnings:
                    plan_response += (
                        "\n\nWarning: the following steps are too vague to be verifiable:\n"
                        + "\n".join(f"  - {w}" for w in _vague_warnings)
                        + "\nRevise these steps to name the specific tools or outcomes expected."
                    )
                return plan_response

            task_plan = task_plan_state.get("plan")
            if task_plan is None:
                return (
                    'No task plan exists yet. Create one first with `action="create"`.'
                )

            if action == "read":
                await task_handle.record_step(
                    "task_plan_read",
                    iteration=iteration,
                    details={"pending_count": len(task_plan.pending_steps())},
                )
                return "Current task plan:\n" + task_plan.format_for_model()

            if action == "complete":
                try:
                    step_id = int(args.get("step_id"))
                except (TypeError, ValueError):
                    return "Error: `step_id` must be a 1-based integer."

                # Warn if no real tool calls were observed since the last completion.
                # This is a soft guard — it does not block completion, but prompts the
                # model to verify it actually performed the step's required work.
                tool_calls_since = task_plan_state.get("tool_calls_since_complete", 0)
                warning_prefix = ""
                if tool_calls_since == 0:
                    warning_prefix = (
                        "Warning: no tool calls were observed since the previous step "
                        "completion. Verify that you have performed the required tool "
                        "calls for this step before marking it done.\n\n"
                    )

                current_step = task_plan.current_step()
                if not task_plan.complete_step(step_id):
                    if current_step is None:
                        return "Error: there is no current step to complete."
                    return (
                        "Error: steps must be completed in order. "
                        f"The current step is {current_step.id}: {current_step.description}"
                    )

                # Reset per-step validation counter after successful completion.
                task_plan_state["tool_calls_since_complete"] = 0
                # Signal _run_loop to reset the consecutive nudge counter (5.5).
                task_plan_state["_nudge_reset_requested"] = True

                next_step = task_plan.current_step()
                await task_handle.record_step(
                    "task_step_completed",
                    iteration=iteration,
                    details={
                        "step_id": step_id,
                        "next_step_id": next_step.id if next_step else None,
                        "tool_calls_observed": tool_calls_since,
                    },
                )
                await task_handle.update_metadata(
                    task_plan=task_plan.to_checklist(),
                    task_plan_active=task_plan.has_pending_steps(),
                    task_plan_pending_count=len(task_plan.pending_steps()),
                )
                completed_label = (
                    task_plan.step_label(current_step)
                    if current_step is not None
                    else f"step {step_id}"
                )
                if next_step is None:
                    await self._emit_task_status(
                        ctx=ctx,
                        message=plan_final_status_message(task_plan),
                    )
                    return (
                        warning_prefix
                        + f"Completed step {step_id}. All tracked steps are now done.\n"
                        + task_plan.format_for_model()
                    )
                await self._emit_task_status(
                    ctx=ctx,
                    message=STATUS_STEP_COMPLETED.format(step_desc=completed_label)
                    + STATUS_STEP_NEXT.format(
                        next_desc=task_plan.step_label(next_step)
                    ),
                )
                return (
                    warning_prefix
                    + f"Completed step {step_id}. Next step is {next_step.id}: "
                    f"{next_step.description}\n{task_plan.format_for_model()}"
                )

            if action == "skip":
                try:
                    step_id = int(args.get("step_id"))
                except (TypeError, ValueError):
                    return "Error: `step_id` must be a 1-based integer."

                reason = str(args.get("reason") or "").strip()
                if not reason:
                    return (
                        "Error: `reason` is required when action=skip. "
                        "Provide a specific explanation for why this step cannot be performed."
                    )

                current_step = task_plan.current_step()
                if not task_plan.skip_step(step_id, reason):
                    if current_step is None:
                        return "Error: there is no current step to skip."
                    return (
                        "Error: steps must be processed in order. "
                        f"The current step is {current_step.id}: {current_step.description}"
                    )

                # Reset per-step validation counter after skip.
                task_plan_state["tool_calls_since_complete"] = 0
                # Signal _run_loop to reset the consecutive nudge counter (5.5).
                task_plan_state["_nudge_reset_requested"] = True

                next_step = task_plan.current_step()
                await task_handle.record_step(
                    "task_step_skipped",
                    iteration=iteration,
                    details={
                        "step_id": step_id,
                        "reason": reason,
                        "next_step_id": next_step.id if next_step else None,
                    },
                )
                await task_handle.update_metadata(
                    task_plan=task_plan.to_checklist(),
                    task_plan_active=task_plan.has_pending_steps(),
                    task_plan_pending_count=len(task_plan.pending_steps()),
                )
                skipped_label = (
                    task_plan.step_label(current_step)
                    if current_step is not None
                    else f"step {step_id}"
                )
                if next_step is None:
                    await self._emit_task_status(
                        ctx=ctx,
                        message=plan_final_status_message(task_plan),
                    )
                    return (
                        f"Skipped step {step_id} ({reason}). All tracked steps are now done or skipped.\n"
                        + task_plan.format_for_model()
                    )
                await self._emit_task_status(
                    ctx=ctx,
                    message=STATUS_STEP_SKIPPED_WITH_NEXT.format(
                        step_desc=skipped_label.rstrip(". "),
                        next_desc=task_plan.step_label(next_step),
                    ),
                )
                return (
                    f"Skipped step {step_id} (reason: {reason}). "
                    f"Next step is {next_step.id}: {next_step.description}\n"
                    + task_plan.format_for_model()
                )

            if action == "append":
                # 5.6 — Append new steps without re-creating the plan.
                raw_new_steps = args.get("steps")
                if not isinstance(raw_new_steps, list):
                    return "Error: `steps` must be an array of step descriptions."
                new_steps_text = [
                    str(s).strip() for s in raw_new_steps if str(s).strip()
                ]
                if not new_steps_text:
                    return "Error: `steps` must contain at least one non-empty step."
                if not task_plan:
                    return (
                        "Error: no task plan exists yet. "
                        "Use action=create to start a plan first."
                    )
                next_id = max((s.id for s in task_plan.steps), default=0) + 1
                for text in new_steps_text:
                    task_plan.steps.append(TaskStep(id=next_id, description=text))
                    next_id += 1
                await task_handle.record_step(
                    "task_plan_appended",
                    iteration=iteration,
                    details={"new_steps": new_steps_text},
                )
                await task_handle.update_metadata(
                    task_plan=task_plan.to_checklist(),
                    task_plan_active=task_plan.has_pending_steps(),
                    task_plan_pending_count=len(task_plan.pending_steps()),
                )
                return (
                    f"Appended {len(new_steps_text)} step(s) to the plan.\n"
                    + task_plan.format_for_model()
                )

            return "Error: `action` must be one of create, read, complete, skip, or append."

        return task_tracker_handler

    @staticmethod
    def _task_plan_pending_checklist(
        task_plan: Optional[InLoopTaskPlan],
    ) -> Optional[List[Dict[str, str]]]:
        """Return checklist of non-done steps (pending, in_progress, and skipped).

        Includes intentionally skipped steps with their reasons so the forced-
        termination model call can report them accurately rather than silently
        omitting them.  Returns ``None`` only when the plan is absent or every
        step is ``done``.
        """
        if task_plan is None:
            return None
        non_done = [s for s in task_plan.steps if s.status != "done"]
        if not non_done:
            return None
        checklist = []
        for s in non_done:
            entry: Dict[str, str] = {"item": s.description, "status": s.status}
            if s.status == "skipped" and s.skip_reason:
                entry["skip_reason"] = s.skip_reason
            checklist.append(entry)
        return checklist

    # ---------------------------------------------------------------------------
    # Static / pure utilities (co-located for testability)
    # ---------------------------------------------------------------------------

    @staticmethod
    def _skill_first_utterance_suggests_smalltalk(
        cfg: SkillRunConfig,
        utterance: str,
        candidate_response: Optional[str],
        score: float,
    ) -> bool:
        cand = (candidate_response or "").strip()
        if len(cand) < cfg.conversational_min_response_chars:
            return False
        text = (utterance or "").strip()
        for expr in cfg.conversational_skip_patterns or []:
            try:
                if re.search(expr, text, re.IGNORECASE | re.UNICODE):
                    return True
            except re.error:
                pass
        if not cfg.skill_first_conversational_heuristic:
            return False
        if len(text) > cfg.conversational_short_utterance_max_chars:
            return False
        tokens = SkillCatalog._normalize_tokens(text)
        if len(tokens) > cfg.conversational_short_utterance_max_tokens:
            return False
        return float(score) < cfg.conversational_heuristic_max_relevance

    @staticmethod
    def _candidate_mentions_discovered_skills(
        candidate: Optional[str], discovered_skills: Optional[Dict[str, Any]]
    ) -> bool:
        if not candidate or not discovered_skills:
            return False
        for name in discovered_skills:
            n = (name or "").strip()
            if not n:
                continue
            if re.search(
                r"(?i)(?<![\w-])" + re.escape(n.lower()) + r"(?![\w-])", candidate
            ):
                return True
        return False

    @staticmethod
    def _update_best_candidate(
        current: Optional[str], candidate: Optional[str]
    ) -> Optional[str]:
        c = (candidate or "").strip()
        if not c:
            return current
        if not (current or "").strip():
            return c
        return c if len(c) > len((current or "").strip()) else current

    @staticmethod
    def _is_degenerate_response(r: Optional[str], *, max_chars: int = 25) -> bool:
        t = (r or "").strip()
        if not t:
            return True
        low = t.lower().rstrip(".!?")
        acks = {
            "ok",
            "understood",
            "done",
            "yes",
            "no",
            "got it",
            "sure",
            "yep",
            "nope",
            "k",
        }
        if low in acks and len(t) <= max_chars:
            return True
        if low in {"thanks", "thank you"} and len(t) <= 16:
            return True
        return len(t) <= 8

    def _should_prefer_best_over_candidate(
        self, cfg: SkillRunConfig, candidate: str, best: Optional[str]
    ) -> bool:
        if not best or not best.strip():
            return False
        c = (candidate or "").strip()
        b = best.strip()
        if self._is_degenerate_response(
            candidate, max_chars=cfg.degenerate_response_max_chars
        ):
            if not self._is_degenerate_response(
                best, max_chars=cfg.degenerate_response_max_chars
            ):
                return True
        if (
            len(b) >= cfg.conversational_min_response_chars
            and c
            and len(c) < max(1.0, cfg.best_candidate_shrink_ratio * float(len(b)))
        ):
            return True
        return False

    @staticmethod
    def _check_step_specificity(steps: List[str]) -> List[str]:
        """Flag steps that are too vague to meaningfully describe work.

        Returns a list of warning messages for vague steps (empty if all steps
        are specific).  This is a soft guard — the plan is still created, but
        operators are warned that the model may be bypassing the plan-first gate
        with a dummy plan.
        """
        _VAGUE_PATTERNS: Tuple[str, ...] = (
            r"\bdo\s+(the\s+)?(work|task|job|thing|it|request|stuff)\b",
            r"\bcomplete\s+(the\s+)?(task|work|request|job)\b",
            r"\bhandle\s+(the\s+)?(request|task|work|it)\b",
            r"\bprocess\s+(the\s+)?(request|input|task)\b",
            r"\bfulfill\s+(the\s+)?(request|task)\b",
            r"\bperform\s+(the\s+)?(task|work|action|request)\b",
            r"\bexecute\s+(the\s+)?(task|plan|work)\b",
            r"\bdo\s+what\s+(was|is|the\s+user)\s+(asked|requested|said)\b",
            r"\banswer\s+(the\s+)?(question|user|query)\s*$",
            r"\brespond\s+to\s+(the\s+)?(user|request|query)\s*$",
        )
        _compiled = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _VAGUE_PATTERNS]
        warnings: List[str] = []
        for step in steps:
            for pat in _compiled:
                if pat.search(step):
                    warnings.append(
                        f"Step '{step[:80]}' is trivially vague — it does not "
                        f"describe the concrete actions or tools needed. "
                        f"Break it into specific, verifiable sub-steps."
                    )
                    break
        return warnings

    @staticmethod
    def _check_plan_faithfulness(
        response: str,
        task_plan: Optional[InLoopTaskPlan],
    ) -> str:
        """Deterministic backstop: detect and replace fabricated completion claims.

        For every non-done step (skipped, pending, in_progress), scans the
        response for segments that contain step-description content tokens AND
        a completion-signal word.  When a contradiction is found the offending
        segment is replaced with an accurate note citing the recorded reason.

        Changes vs. original:
        - Covers all non-done steps, not just skipped ones.
        - Strips English stop words before computing overlap so short function
          words ("the", "to", "a") do not inflate the match count.
        - Threshold lowered to 1 content token + completion signal (after stop
          words are removed, one meaningful content word is sufficient evidence).
        - Splits on newlines and markdown bullets in addition to sentence endings
          so multi-paragraph and bullet-list responses are handled correctly.

        This runs after ``_final_review_pass`` and requires no model call.
        """
        if not task_plan:
            return response
        non_done = [s for s in task_plan.steps if s.status != "done"]
        if not non_done:
            return response

        _COMPLETION_SIGNALS: frozenset = frozenset(
            {
                "saved",
                "save",
                "written",
                "write",
                "stored",
                "store",
                "assimilated",
                "assimilate",
                "uploaded",
                "upload",
                "created",
                "added",
                "complete",
                "completed",
                "done",
                "finished",
                "succeeded",
                "success",
                "performed",
                "executed",
            }
        )

        # Common English stop words that carry no semantic specificity.
        _STOP_WORDS: frozenset = frozenset(
            {
                "a",
                "an",
                "the",
                "and",
                "or",
                "but",
                "in",
                "on",
                "at",
                "to",
                "for",
                "of",
                "with",
                "by",
                "from",
                "is",
                "it",
                "its",
                "be",
                "as",
                "this",
                "that",
                "was",
                "are",
                "been",
                "have",
                "has",
                "had",
                "do",
                "did",
                "my",
                "your",
                "our",
                "their",
                "we",
                "i",
                "he",
                "she",
                "they",
                "not",
                "no",
                "so",
                "if",
                "up",
                "out",
            }
        )

        # Split on sentence boundaries, newlines, and markdown bullet markers.
        segments = re.split(r"(?<=[.!?])\s+|\n+|(?<=\s)-\s+", response)
        replacements_made = 0

        for step in non_done:
            raw_tokens = set(SkillCatalog._normalize_tokens(step.description))
            step_tokens = raw_tokens - _STOP_WORDS
            if not step_tokens:
                # Fall back to full token set when description is stop-word-only.
                step_tokens = raw_tokens
            for i, segment in enumerate(segments):
                raw_sent = set(SkillCatalog._normalize_tokens(segment))
                sent_tokens = raw_sent - _STOP_WORDS
                overlap = step_tokens & sent_tokens
                has_signal = bool(raw_sent & _COMPLETION_SIGNALS)
                # 1 content-token overlap + completion signal is sufficient after
                # stop-word removal — meaningful terms like "writeable" or
                # "assimilate" uniquely identify the step.
                if len(overlap) >= 1 and has_signal:
                    reason = step.skip_reason or "this step could not be completed"
                    segments[i] = f"(Note: {step.description} — {reason})"
                    replacements_made += 1
                    logger.warning(
                        "SkillAction._check_plan_faithfulness: replaced fabricated "
                        "completion claim for step %d [%s] (%r). Original: %r",
                        step.id,
                        step.status,
                        step.description[:60],
                        segment[:120],
                    )

        if replacements_made:
            return " ".join(segments)
        return response

    @staticmethod
    def _extract_tool_intent(args_str: str, max_len: int = 80) -> str:
        _FRAMES = {
            "query": "search for",
            "search": "search for",
            "skill_name": "activate",
            "input": "process",
            "message": "send",
            "text": "work through",
            "question": "answer",
            "command": "run",
            "url": "fetch",
        }
        _TARGET_KEYS = frozenset({"name", "file_path", "path", "filename"})
        _PREPS = {"file_path": "to", "path": "in", "filename": "to"}
        try:
            args = json.loads(args_str) if args_str else {}
        except (json.JSONDecodeError, TypeError):
            raw = (args_str or "").strip()[:max_len]
            return f"work with {raw}" if raw else "figure out the next step"
        for key in _FRAMES:
            if key in args:
                value = str(args[key]).strip()
                if not value:
                    continue
                phrase = f"{_FRAMES[key]} {value}"
                return (
                    phrase[: max_len - 3] + "..." if len(phrase) > max_len else phrase
                )
        for key in _TARGET_KEYS:
            if key in args:
                value = str(args[key]).strip()
                if not value:
                    continue
                prep = _PREPS.get(key, "for")
                phrase = f"work {prep} {value}"
                return (
                    phrase[: max_len - 3] + "..." if len(phrase) > max_len else phrase
                )
        if args:
            key, value = next(iter(args.items()))
            value = str(value)
            phrase = f"work with {key}={value}"
            return phrase[: max_len - 3] + "..." if len(phrase) > max_len else phrase
        return "figure out the next step"

    @staticmethod
    def _format_result_preview(
        content: str, max_lines: int = 2, max_chars: int = 200
    ) -> str:
        if not content:
            return "no output"
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        _STRUCTURAL = (
            "Skill loaded:",
            "Error:",
            "No skills found",
            "Available skills:",
        )
        for prefix in _STRUCTURAL:
            if first_line.startswith(prefix):
                if prefix == "Skill loaded:":
                    skill_name = first_line[len(prefix) :].strip()
                    for line in content.strip().splitlines()[1:]:
                        if line.strip().startswith("Newly available tools:"):
                            tools = line.strip()[
                                len("Newly available tools:") :
                            ].strip()
                            return f"loaded {skill_name}; tools now available: {tools}"
                    return f"loaded {skill_name}"
                return first_line[:max_chars]
        lines = content.strip().splitlines()
        preview_lines = [ln.rstrip() for ln in lines[:max_lines] if ln.strip()]
        if not preview_lines:
            return "no output"
        preview = "\n".join(preview_lines)
        if len(preview) > max_chars:
            preview = preview[: max_chars - 3] + "..."
        if len(lines) > max_lines:
            remaining = len(lines) - max_lines
            preview += f"\n… plus {remaining} more line{'s' if remaining != 1 else ''}"
        return preview

    @staticmethod
    def _clean_tool_name(name: str) -> str:
        if "__" in name:
            return name.rsplit("__", 1)[-1]
        return name

    @staticmethod
    def _resolve_thinking_token_count(model_result: Any) -> int:
        t = int(getattr(model_result, "thinking_tokens", None) or 0)
        if t:
            return t
        m = getattr(model_result, "metrics", None) or {}
        ctd = m.get("completion_tokens_details")
        if isinstance(ctd, dict) and ctd.get("reasoning_tokens") is not None:
            try:
                return int(ctd.get("reasoning_tokens") or 0)
            except (TypeError, ValueError):
                pass
        tc = getattr(model_result, "thinking_content", None) or ""
        if isinstance(tc, str) and len(tc) > 0:
            return max(1, len(tc) // 4)
        return 0

    @staticmethod
    def _extract_result_attributions(
        content: str, tool_call_id: str
    ) -> List[Dict[str, str]]:
        patterns = {
            "id": re.compile(r"\b[a-fA-F0-9]{8,}\b"),
            "uuid": re.compile(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                re.I,
            ),
            "number": re.compile(r"\b\d{4,}\b"),
            "version": re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"),
            "path": re.compile(r"(?:[a-zA-Z]:)?[\\/][\w\-./\\]+"),
            "url": re.compile(r"https?://[\w\-.]+(?:/[\w\-./?&=]*)?"),
            "quoted": re.compile(r'"([^"]{4,})"'),
            "email": re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}"),
        }
        attributions: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for claim_type, pattern in patterns.items():
            for match in pattern.finditer(content):
                claim = match.group(0)
                if claim in seen:
                    continue
                seen.add(claim)
                attributions.append(
                    {
                        "claim_type": claim_type,
                        "claim": claim,
                        "source_tool_call_id": tool_call_id,
                    }
                )
        return attributions

    @staticmethod
    def _verify_grounding(
        response: str, attributions: List[Dict[str, str]], strict: bool = True
    ) -> Tuple[str, List[str]]:
        if not attributions:
            return response, []
        attributed: Set[str] = set()
        for attr in attributions:
            claim = attr.get("claim", "")
            if claim:
                attributed.add(claim.lower())
                attributed.add(claim.lower().strip('"'))
        candidate_patterns = [
            re.compile(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                re.I,
            ),
            re.compile(r"https?://[\w\-.]+(?:/[\w\-./?&=]*)?"),
            re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}"),
            # Negative lookbehind avoids matching path-like substrings inside URLs (e.g. //host)
            re.compile(r"(?<![:/\\])(?:[a-zA-Z]:)?[/\\][\w\-./\\]+"),
            re.compile(r"\b[a-fA-F0-9]{12,}\b"),
        ]
        unattributed: List[str] = []
        accepted_spans: List[Tuple[int, int]] = []

        def _overlaps(span: Tuple[int, int]) -> bool:
            s, e = span
            return any(
                a <= s < b or a < e <= b or (s <= a and e >= b)
                for a, b in accepted_spans
            )

        for pat in candidate_patterns:
            for match in pat.finditer(response):
                span = (match.start(), match.end())
                if _overlaps(span):
                    continue
                accepted_spans.append(span)
                raw = match.group(0)
                if raw.lower().strip('"') not in attributed:
                    unattributed.append(raw)
        if strict and unattributed:
            for claim in unattributed:
                response = response.replace(claim, f"{claim} [unverified]", 1)
        return response, unattributed

    # ---------------------------------------------------------------------------
    # Reasoning config
    # ---------------------------------------------------------------------------

    @staticmethod
    def _build_reasoning_cfg(cfg: SkillRunConfig) -> ReasoningModelConfig:
        budget = int(cfg.reasoning_budget_tokens or 0)
        reasoning_enabled = cfg.reasoning_enabled
        if reasoning_enabled is None:
            effort = str(cfg.reasoning_effort or "").strip()
            if budget > 0 or effort:
                reasoning_enabled = True
            elif isinstance(cfg.reasoning_extra, dict) and (
                cfg.reasoning_extra.get("think") is True
                or cfg.reasoning_extra.get("enabled") is True
            ):
                reasoning_enabled = True
        effort_clean = (
            str(cfg.reasoning_effort).strip() if cfg.reasoning_effort else None
        )
        return ReasoningModelConfig(
            reasoning_effort=effort_clean,
            reasoning_budget_tokens=budget,
            reasoning_enabled=reasoning_enabled,
            reasoning_extra=(
                cfg.reasoning_extra if isinstance(cfg.reasoning_extra, dict) else None
            ),
            mirror_assistant_stream_as_thoughts=cfg.mirror_assistant_stream_as_thoughts,
            profile="reasoning",
        )

    @staticmethod
    def _reasoning_cfg_for_profile(
        base: ReasoningModelConfig, profile: str
    ) -> ReasoningModelConfig:
        return ReasoningModelConfig(
            reasoning_effort=base.reasoning_effort,
            reasoning_budget_tokens=base.reasoning_budget_tokens,
            reasoning_enabled=base.reasoning_enabled,
            reasoning_extra=base.reasoning_extra,
            mirror_assistant_stream_as_thoughts=base.mirror_assistant_stream_as_thoughts,
            profile=profile,
        )

    # ---------------------------------------------------------------------------
    # Misc helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _initial_task_metadata(ctx: SkillRunContext) -> Dict[str, Any]:
        from datetime import datetime, timezone

        cfg = ctx.config
        return {
            "skills": cfg.skills,
            "skills_source": cfg.skills_source,
            "strict_grounding": cfg.strict_grounding,
            "plan_first": cfg.plan_first,
            "final_review": cfg.final_review,
            "task_nudge_retry_limit": cfg.task_nudge_retry_limit,
            "max_skill_activations": cfg.max_skill_activations,
            "stuck_detection_window": cfg.stuck_detection_window,
            "max_midcourse_corrections": cfg.max_midcourse_corrections,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "iterations": 0,
            "tools_called": [],
            "thinking_tokens_used": 0,
            "steps": [],
            "completed_at": None,
            "total_duration_seconds": None,
            "helper_tools_called": [],
            "meta_intent_detected": None,
            "retry_nudges_fired": 0,
            "task_plan": [],
            "task_plan_active": False,
            "task_plan_pending_count": 0,
            "best_candidate_length": None,
        }


class _AgentShim:
    """Minimal shim exposing the attributes SkillCatalog/ToolExecutor expect from a visitor."""

    def __init__(
        self,
        agent: Any,
        action_resolver: Optional[ActionResolver],
        user_id: Optional[str] = None,
        conversation: Any = None,
        interaction: Any = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._agent = agent
        self.action_resolver = action_resolver
        self.user_id = (user_id or "").strip() or None
        # Many local skill tools rely on visitor.conversation for persisted context.
        # Keep this shim compatible with both SkillCatalog (discovery) and ToolExecutor (dispatch).
        self.conversation = conversation
        self.interaction = interaction
        self.session_id = session_id
