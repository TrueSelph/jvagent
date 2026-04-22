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
from typing import Any, Dict, List, Optional, Set, Tuple

from jvagent.action.model.language.base import ReasoningModelConfig
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.context_compactor import CompactorConfig, ContextCompactor
from jvagent.action.skill.loop_checkpoint import CheckpointStore, LoopCheckpoint
from jvagent.action.skill.loop_context import LoopContext, LoopContextConfig
from jvagent.action.skill.prompts import (
    ERROR_ANNOUNCE_TEMPLATE,
    FINAL_REVIEW_PROMPT,
    FORCED_TERMINATION_PROMPT_NO_CHECKLIST,
    FORCED_TERMINATION_PROMPT_TEMPLATE,
    GROUNDING_INSTRUCTION_TEMPLATE,
    LIST_SKILLS_TOOL_DESCRIPTION,
    MONOLOGUE_OPENERS,
    MONOLOGUE_RESULT_ERR,
    MONOLOGUE_RESULT_OK,
    PENDING_STEPS_NUDGE_PROMPT,
    PENDING_STEPS_NUDGE_PROMPT_FINAL,
    PLAN_SKILLS_TOOL_DESCRIPTION,
    PROGRESS_CHECK_PROMPT_TEMPLATE,
    READ_SKILL_RESULT_TEMPLATE,
    SKILL_AGENT_SYSTEM_PROMPT,
    SKILL_FIRST_RETRY_PROMPT,
    SKILL_SEARCH_TOOL_DESCRIPTION,
    STATUS_ALL_STEPS_DONE,
    STATUS_FINAL_REVIEW,
    STATUS_PLAN_CREATED,
    STATUS_STEP_COMPLETED,
    STATUS_STEP_NEXT,
    STUCK_DETECTION_PROMPT,
    TOOL_CALL_ANNOUNCE_TEMPLATE,
    TOOL_RESULT_ANNOUNCE_TEMPLATE,
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

# Built-in coordination / catalog navigation tools — not considered "real" tool evidence.
_SKILL_HELPER_TOOL_NAMES: frozenset = frozenset(
    ("list_skills", "skill_search", "plan_skills", "read_skill", "task_tracker")
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
                        "\n\nOverride: Skip plan-first behavior unless the user "
                        "explicitly asks for a plan."
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
                    status=result.termination_reason,
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
                termination_reason=TerminationReason.ERROR.value,
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
        _visitor_shim = _AgentShim(ctx.agent, action_resolver, user_id=ctx.user_id)
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
                ),
            )

            if cfg.enable_skill_helper_tools:
                self._register_skill_helper_tools(
                    tool_executor, skill_catalog, discovered_skills
                )

        if not tool_executor.get_tool_names():
            logger.warning(
                "SkillAction: No tools available; proceeding in reasoning-only mode"
            )

        # --- Skill preflight: deterministic capability check before first model call ---
        if not skill_catalog.is_empty:
            action_resolver = ActionResolver(ctx.agent) if ctx.agent else None
            preflight_failures = await skill_catalog.preflight_check(
                action_resolver=action_resolver,
                tool_executor=tool_executor,
            )
            if preflight_failures:
                # Log structured failures into context (non-fatal; loop proceeds)
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
        termination_reason = TerminationReason.COMPLETED.value
        loop_phase = LoopPhase.INIT

        stuck_detector = StuckDetector(
            StuckDetectorConfig(
                window_size=max(1, int(cfg.stuck_detection_window or 1)),
                max_corrections=cfg.max_midcourse_corrections,
                intent_similarity_threshold=0.7,
            )
        )

        skill_first_retries = 0
        task_nudges = 0
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
                    "Create, read, complete, or skip steps in the in-loop task plan. "
                    "For multi-step tasks, create the plan first, then complete each step "
                    "before moving to the next. Use skip (with a reason) when a step "
                    "cannot be performed so the plan can advance."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "read", "complete", "skip"],
                        },
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ordered step descriptions used when action=create.",
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
                task_plan_state=task_plan_state,
                task_handle=task_handle,
                iteration_getter=lambda: iteration,
                review_enabled=cfg.final_review,
            ),
        )

        tools_ever_called: Set[str] = set()
        nontrivial_tools_ever_called: Set[str] = set()
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
                )
                termination_reason = TerminationReason.TIME_CAP.value
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
                    termination_reason_candidate=termination_reason,
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
                action = recovery_policy.decide(failure)
                logger.warning(
                    "SkillAction: model call failed at iter %d (%s): %s → %s",
                    iteration,
                    loop_phase.value,
                    model_exc,
                    action,
                )
                if action == "terminate":
                    termination_reason = TerminationReason.ERROR.value
                    final_response = await self._force_termination(
                        messages,
                        tools,
                        ctx,
                        base_model_kwargs,
                        reasoning_cfg,
                        checklist=self._task_plan_pending_checklist(
                            task_plan_state["plan"]
                        ),
                    )
                    break
                # retry: continue to next iteration (messages unchanged)
                await task_handle.record_step(
                    "model_error",
                    iteration=iteration,
                    details={"error": str(model_exc), "action": action},
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
                loop_phase = LoopPhase.OBSERVE
                candidate_response = await model_result.get_response()
                if not candidate_response and model_result.response:
                    candidate_response = model_result.response

                best_candidate = self._update_best_candidate(
                    best_candidate, candidate_response
                )
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
                    messages.append(
                        {"role": "assistant", "content": candidate_response or ""}
                    )
                    messages.append(
                        {"role": "user", "content": SKILL_FIRST_RETRY_PROMPT}
                    )
                    skill_first_retries += 1
                    continue

                # Pending-step gate: task-plan state is the source of truth.
                task_plan = task_plan_state["plan"]
                if task_plan is not None and task_plan.has_pending_steps():
                    if task_nudges < cfg.task_nudge_retry_limit:
                        loop_phase = LoopPhase.NUDGE
                        is_final_nudge = task_nudges == cfg.task_nudge_retry_limit - 1
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
                        await task_handle.update_metadata(
                            retry_nudges_fired=retry_nudges
                        )
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
                        continue
                    else:
                        # Nudge limit exhausted with steps still pending — escalate to
                        # forced termination with checklist rather than silently accepting.
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
                        )
                        termination_reason = TerminationReason.ITER_CAP.value
                        loop_phase = LoopPhase.TERMINATE
                        break

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
                termination_reason = TerminationReason.COMPLETED.value
                loop_phase = LoopPhase.TERMINATE
                break

            # ---- Tool calls ----
            tool_calls = model_result.tool_calls
            stuck_result = stuck_detector.record(tool_calls)
            loop_phase = LoopPhase.TOOL_DISPATCH

            tool_names = [
                tc.get("function", {}).get("name", "unknown") for tc in tool_calls
            ]
            for n in tool_names:
                if n and n != "unknown":
                    tools_ever_called.add(n)
                    if not (
                        n in _SKILL_HELPER_TOOL_NAMES or n.startswith("skill_hub__")
                    ):
                        nontrivial_tools_ever_called.add(n)

            # Helper tool tracking
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
                for idx, tc in enumerate(tool_calls):
                    tool_name = tc.get("function", {}).get("name", "unknown")
                    display_name = self._clean_tool_name(tool_name)
                    intent = self._extract_tool_intent(
                        tc.get("function", {}).get("arguments", "")
                    )
                    opener = MONOLOGUE_OPENERS[
                        (iteration + idx) % len(MONOLOGUE_OPENERS)
                    ]
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
                    termination_reason_candidate=termination_reason,
                )
                await checkpoint_store.save(ckpt)

            tool_start = time.monotonic()
            tool_result_messages = await tool_executor.dispatch(tool_calls)
            tool_duration_ms = int((time.monotonic() - tool_start) * 1000)

            # Record raw evidence
            if cfg.enable_evidence_log:
                for tr_msg, tc in zip(tool_result_messages, tool_calls):
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
                for tc in tool_calls
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
                    )
                    termination_reason = TerminationReason.STUCK.value
                    messages.extend(tool_result_messages)
                    break
                else:
                    messages.append({"role": "user", "content": stuck_result})

            messages.extend(tool_result_messages)

            # Reset the nudge counter whenever productive (non-helper) tool calls were
            # dispatched.  This ensures each new termination attempt gets its full quota
            # of nudges regardless of how many occurred in earlier rounds.
            if any(n not in _SKILL_HELPER_TOOL_NAMES for n in tool_names):
                task_nudges = 0
                # Track productive calls for per-step validation in task_tracker complete.
                task_plan_state["tool_calls_since_complete"] = task_plan_state.get(
                    "tool_calls_since_complete", 0
                ) + sum(
                    1
                    for n in tool_names
                    if n not in _SKILL_HELPER_TOOL_NAMES and n != "task_tracker"
                )

            # Evidence-aware compaction (replaces bare truncation)
            messages = compactor.compact(messages, evidence_log=evidence_log)

        # ---- Post-loop handling ----
        if (
            not final_response
            and termination_reason == TerminationReason.COMPLETED.value
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
            )
            termination_reason = TerminationReason.ITER_CAP.value

        if not final_response:
            final_response = (
                "I was unable to complete the task within the allowed steps."
            )
            if termination_reason == TerminationReason.COMPLETED.value:
                termination_reason = TerminationReason.ITER_CAP.value
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
                details = ""
                if task_plan is not None and task_plan.steps:
                    details = f" - verifying {len(task_plan.steps)} completed steps"
                await self._emit_task_status(
                    ctx=ctx,
                    message=STATUS_FINAL_REVIEW.format(details=details),
                )
                final_response = await self._final_review_pass(
                    messages=messages,
                    candidate_response=final_response,
                    ctx=ctx,
                    base_model_kwargs=base_model_kwargs,
                    reasoning_cfg=reasoning_cfg,
                )

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
                "termination_reason": termination_reason,
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

        # Clear checkpoint on clean exit
        if cfg.enable_checkpoints:
            await checkpoint_store.clear()

        # Count any steps that were still pending at termination time.
        _final_plan = task_plan_state.get("plan")
        _skipped_steps = (
            len(_final_plan.pending_steps()) if _final_plan is not None else 0
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
            task_plan_skipped_steps=_skipped_steps,
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
    ) -> str:
        if checklist:
            checklist_text = "\n".join(
                f"- [{c.get('status', 'pending')}] {c.get('item', 'unknown')}"
                for c in checklist
            )
            prompt = FORCED_TERMINATION_PROMPT_TEMPLATE.format(checklist=checklist_text)
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

    async def _final_review_pass(
        self,
        messages: List[Dict[str, Any]],
        candidate_response: str,
        ctx: SkillRunContext,
        base_model_kwargs: Dict[str, Any],
        reasoning_cfg: ReasoningModelConfig,
    ) -> str:
        review_msgs = list(messages)
        review_msgs.append({"role": "assistant", "content": candidate_response})
        review_msgs.append({"role": "user", "content": FINAL_REVIEW_PROMPT})
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
            registered_tools = await tool_executor.activate_skill(skill_name)
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
            return SkillCatalog(discovered_skills).search(query, top_k=top_k)

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
                    },
                    "required": ["query"],
                },
            },
            handler=skill_search_handler,
        )

        async def plan_skills_handler(args):
            query = str(args.get("query", "")).strip()
            top_k = max(1, int(args.get("top_k", 5)))
            matches = SkillCatalog(discovered_skills).search(query, top_k=top_k)
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
                    },
                    "required": ["query"],
                },
            },
            handler=plan_skills_handler,
        )

    # ---------------------------------------------------------------------------
    # Decision / task-tracker helpers
    # ---------------------------------------------------------------------------

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
        if candidate_response and self._candidate_mentions_discovered_skills(
            candidate_response, discovered_skills
        ):
            if (
                len((candidate_response or "").strip())
                >= cfg.conversational_min_response_chars
            ):
                return False
        catalog = SkillCatalog(discovered_skills)
        score = catalog.top_relevance_score(utterance or "")
        if candidate_response and self._skill_first_utterance_suggests_smalltalk(
            cfg, utterance, candidate_response, score
        ):
            return False
        return score >= cfg.skill_first_retry_min_relevance

    def _make_task_tracker_handler(
        self,
        *,
        ctx: SkillRunContext,
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
                return "Task plan created:\n" + task_plan.format_for_model()

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
                        message=STATUS_ALL_STEPS_DONE,
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
                        message=STATUS_ALL_STEPS_DONE,
                    )
                    return (
                        f"Skipped step {step_id} ({reason}). All tracked steps are now done or skipped.\n"
                        + task_plan.format_for_model()
                    )
                await self._emit_task_status(
                    ctx=ctx,
                    message=STATUS_STEP_COMPLETED.format(
                        step_desc=f"skipped: {skipped_label}"
                    )
                    + STATUS_STEP_NEXT.format(
                        next_desc=task_plan.step_label(next_step)
                    ),
                )
                return (
                    f"Skipped step {step_id} (reason: {reason}). "
                    f"Next step is {next_step.id}: {next_step.description}\n"
                    + task_plan.format_for_model()
                )

            return "Error: `action` must be one of create, read, complete, or skip."

        return task_tracker_handler

    @staticmethod
    def _task_plan_pending_checklist(
        task_plan: Optional[InLoopTaskPlan],
    ) -> Optional[List[Dict[str, str]]]:
        if task_plan is None or not task_plan.has_pending_steps():
            return None
        return task_plan.to_checklist(pending_only=True)

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
    ) -> None:
        self._agent = agent
        self.action_resolver = action_resolver
        self.user_id = (user_id or "").strip() or None
