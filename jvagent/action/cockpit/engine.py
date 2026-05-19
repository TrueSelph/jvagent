"""CockpitEngine: think-act-observe loop with single-step iteration.

Each call to ``step()`` executes exactly one model call. The calling action
controls iteration by checking the step result and re-adding itself to the
walker walk path when more steps are needed (walker-revisit pattern).
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from jvagent.action.cockpit.context import (
    CockpitContext,
    CockpitResult,
    CockpitState,
    CockpitStepResult,
)
from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.action.cockpit.prompts import (
    CAPABILITY_SEARCH_NOTE,
    COCKPIT_SYSTEM_PROMPT,
    SECURITY_BLOCK,
    TASK_PLANNING_BLOCK,
)
from jvagent.action.cockpit.session import get_session, get_session_optional
from jvagent.tooling.tool_executor import ToolExecutionEngine
from jvagent.tooling.tool_registry import ToolRegistry
from jvagent.tooling.tool_serializer import ToolSerializer

logger = logging.getLogger(__name__)


def _tool_call_signature(tc: Dict[str, Any]) -> str:
    """Stable signature of a tool call (name + arguments fingerprint).

    Used for stuck detection: identical (name, args) across iterations is
    genuine repetition; same name with different args is legitimate refinement.
    """
    import hashlib
    import json as _json

    fn = tc.get("function") if isinstance(tc, dict) else None
    name = (fn or {}).get("name", "unknown") if isinstance(fn, dict) else "unknown"
    raw_args = (fn or {}).get("arguments") if isinstance(fn, dict) else None
    if isinstance(raw_args, dict):
        try:
            normalized = _json.dumps(raw_args, sort_keys=True, default=str)
        except Exception:
            normalized = str(raw_args)
    elif isinstance(raw_args, str):
        normalized = raw_args
    else:
        normalized = ""
    fp = hashlib.blake2b(normalized.encode("utf-8"), digest_size=6).hexdigest()
    return f"{name}::{fp}"


# Tools whose output the user has ALREADY seen via the response bus
# (response_publish writes to the user's chat bubble; response_emit_thought
# streams to the Reasoning panel; response_deliver_via_persona triggers
# PersonaAction which also publishes). Emitting tool_call / tool_result /
# tool_progress observability envelopes for these is pure noise — every
# call surfaces as a redundant ``← ok: response_publish`` line under the
# Reasoning panel in jvchat. Skip the envelopes; the underlying publish
# already produced the user-visible artifact.
USER_VISIBLE_TOOL_NAMES = frozenset(
    {
        "response_publish",
        "response_emit_thought",
        "response_deliver_via_persona",
    }
)


def _suppress_tool_observability(tool_name: str) -> bool:
    """Return True iff tool ``tool_name`` should NOT emit tool_call /
    tool_result / tool_progress thought envelopes.

    Honors :data:`USER_VISIBLE_TOOL_NAMES`. Override by setting env
    ``JVAGENT_COCKPIT_VERBOSE_RESPONSE_TOOLS=true`` to bring the old
    triple-emit back (useful for backend-only consumers that don't
    inspect the chat stream).
    """
    import os

    if os.environ.get("JVAGENT_COCKPIT_VERBOSE_RESPONSE_TOOLS", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }:
        return False
    return tool_name in USER_VISIBLE_TOOL_NAMES


class CockpitEngine:
    """The cockpit think-act-observe engine.

    Instantiates per run. Uses ``ToolExecutionEngine`` for dispatch and
    ``LanguageModelAction.query_messages()`` for model calls.

    Usage (walker-revisit pattern)::

        engine = CockpitEngine(ctx)
        await engine.initialize()
        # Then call step() on each walker visit until final_response.
    """

    def __init__(self, ctx: CockpitContext) -> None:
        self.ctx = ctx
        self._registry: Optional[ToolRegistry] = None
        self._tool_executor: Optional[ToolExecutionEngine] = None
        self._messages: List[Dict[str, Any]] = []
        self._tools_serialized: List[Dict[str, Any]] = []
        self._iteration: int = 0
        self._start: float = 0.0
        self._activated_skills: List[str] = []
        self._recent_tool_names: List[List[str]] = []
        # Per-iteration tool-call signatures (name + arguments fingerprint).
        # Used by _check_stuck to distinguish "calling same tool repeatedly with
        # different args" (legitimate refinement) from "same call over and over"
        # (genuine stuck pattern).
        self._recent_tool_signatures: List[List[str]] = []
        # Auto-tracked trace task: created at initialize, updated per step,
        # completed/failed at termination. Surfaces in active_tasks /
        # completed_tasks on the interaction response.
        self._trace_task: Any = None
        self._initialized: bool = False
        # Idempotency guard for structured tool envelopes (SPEC §7.3).
        # Tracks (tool_call_id, thought_type) pairs we've already
        # published so a single logical envelope is never emitted
        # twice — even if the surrounding loop accidentally calls
        # _emit_tool_call / _emit_tool_result more than once for the
        # same tc.id (walker re-walks, retry paths, parallel tasks,
        # etc.). Membership-only set; never queried for content.
        self._emitted_envelopes: set = set()

    async def initialize(self) -> None:
        """Set up registry, system prompt, history, and initial user message.

        Call once at the start of a cockpit run, before the first ``step()``.
        """
        self._registry = await assemble_cockpit_tools(self.ctx)
        self._tool_executor = ToolExecutionEngine(
            self._registry,
            call_timeout=self.ctx.config.tool_call_timeout,
            max_concurrent=self.ctx.config.max_concurrent_tools,
            sanitize_errors=self.ctx.config.sanitize_tool_errors,
            # Forward the visitor so per-user routing fires for tools that
            # need caller identity — notably MCP filesystem dispatch, which
            # otherwise binds to the default ``_default`` subprocess and
            # writes every user's files into the same shared folder.
            visitor=self.ctx.visitor,
        )

        self._tools_serialized = ToolSerializer.serialize_all(self._registry.list())

        system_prompt = await self._build_system_prompt()
        self._messages = []
        self._messages.append({"role": "system", "content": system_prompt})

        history = await self._build_history()
        self._messages.extend(history)

        self._messages.append({"role": "user", "content": self.ctx.utterance})

        # Lightweight prompt-size telemetry. Writes to debug logs only — useful
        # when tuning the cockpit surface but never on the hot path.
        if logger.isEnabledFor(logging.DEBUG):
            import json as _json

            tools_bytes = sum(len(_json.dumps(t)) for t in self._tools_serialized)
            sys_bytes = len(system_prompt)
            hist_bytes = sum(len(str(m.get("content") or "")) for m in history)
            logger.debug(
                "CockpitEngine.initialize: tools=%d tools_bytes=%d "
                "system_bytes=%d history_messages=%d history_bytes=%d",
                len(self._tools_serialized),
                tools_bytes,
                sys_bytes,
                len(history),
                hist_bytes,
            )

        self._start = time.monotonic()
        self._iteration = 0
        self._activated_skills = list(self.ctx.preloaded_skills)
        self._recent_tool_names = []
        self._recent_tool_signatures = []

        # Auto-track this run as a Task so observability sees structured
        # progress (active_tasks / completed_tasks on the interaction response)
        # even when the model doesn't explicitly call task_create_plan.
        if getattr(self.ctx.config, "auto_track_tasks", True):
            await self._auto_task_start()

        # Structural router→skill dispatch. When the router pre-selected a
        # skill that declares `dispatch:` in its frontmatter, run that tool
        # NOW and inject the synthetic assistant(tool_calls)+tool(result)
        # pair into the message history. The model's first step() then
        # synthesizes from real data instead of being asked to plan and
        # possibly hallucinating or wandering. Runs after _auto_task_start
        # so the synthetic call is recorded on the trace task.
        await self._maybe_pre_dispatch()

        self._initialized = True

    async def step(self) -> CockpitStepResult:
        """Execute one model call and return the result.

        Returns a ``CockpitStepResult`` indicating whether to continue
        (``tool_calls``) or deliver (``final_response`` / terminal states).
        """
        if not self._initialized:
            raise RuntimeError(
                "CockpitEngine.initialize() must be called before step()"
            )

        self._iteration += 1
        elapsed = time.monotonic() - self._start
        cfg = self.ctx.config

        # Budget checks
        if elapsed >= cfg.max_duration_seconds:
            await self._auto_task_finalize(
                success=False,
                result_summary="time budget exceeded",
                reason="time_cap",
            )
            return CockpitStepResult(
                status="timeout",
                final_response="I was unable to complete the task within the time limit.",
                termination_reason=TerminationReason.TIME_CAP,
                iterations=self._iteration,
                duration_seconds=elapsed,
                activated_skills=list(self._activated_skills),
            )

        if self._iteration > cfg.max_iterations:
            await self._auto_task_finalize(
                success=False,
                result_summary="iteration budget exceeded",
                reason="iter_cap",
            )
            return CockpitStepResult(
                status="budget_exhausted",
                final_response=(
                    "I've reached the maximum number of steps for this task without "
                    "completing it. Please let me know if you'd like me to continue."
                ),
                termination_reason=TerminationReason.ITER_CAP,
                iterations=self._iteration,
                duration_seconds=elapsed,
                activated_skills=list(self._activated_skills),
            )

        # If a previous tool call hot-registered new skill tools via
        # ``skill_activate``, the registry has grown but ``_tools_serialized``
        # was captured at startup. Re-serialise before the next model call so
        # the new tools become callable.
        if getattr(self.ctx, "registry_dirty", False) and self._registry is not None:
            self._tools_serialized = ToolSerializer.serialize_all(
                self._registry.list()
            )
            for name in self.ctx.preloaded_skills:
                if name not in self._activated_skills:
                    self._activated_skills.append(name)
            self.ctx.registry_dirty = False
            logger.info(
                "CockpitEngine: tool registry refreshed (dynamic activation); "
                "%d tools now visible to engine",
                len(self._tools_serialized),
            )

        use_stream = bool(self.ctx.stream)
        result = await self.ctx.model_action.query_messages(
            self._messages,
            stream=use_stream,
            tools=self._tools_serialized,
            **self._model_query_kwargs(),
        )

        if use_stream and result.is_streaming:
            await self._consume_stream_with_live_reasoning(result)
        else:
            await self._emit_thinking_thought(result)

        if result.tool_calls:
            # Dispatch all tool calls first — side effects must execute
            # even if the finalized flag is set (e.g. task_update_step
            # alongside response_publish).
            for tc in result.tool_calls:
                fn = tc.get("function", {})
                tc_name = fn.get("name", "unknown")
                logger.debug(
                    "CockpitEngine [%d]: model called tool '%s'",
                    self._iteration,
                    tc_name,
                )

            # Pre-execution structured emit (Integral SPEC §7.3 #1).
            # Lets streaming consumers render "calling X with Y" as
            # soon as the model decides — no waiting for execution.
            # Same gating as ``_emit_tool_progress``.
            if cfg.stream_internal_progress and self.ctx.stream:
                await self._emit_tool_call(result.tool_calls, self._iteration)

            tool_results = await self._tool_executor.dispatch(result.tool_calls)

            # Post-execution structured emit. Same id pairs each
            # result with its prior tool_call envelope so consumers
            # can stitch them together.
            if cfg.stream_internal_progress and self.ctx.stream:
                await self._emit_tool_result(
                    result.tool_calls, tool_results, self._iteration
                )

            self._messages.append(
                {"role": "assistant", "content": None, "tool_calls": result.tool_calls}
            )
            for tr in tool_results:
                self._messages.append(tr.tool_result_message())

            # Track tool names + call signatures (name + args fingerprint) for stuck detection
            tool_names = [
                tc.get("function", {}).get("name", "unknown")
                for tc in result.tool_calls
            ]
            self._recent_tool_names.append(tool_names)
            self._recent_tool_signatures.append(
                [_tool_call_signature(tc) for tc in result.tool_calls]
            )

            # Record this iteration as a step on the auto-tracked trace task.
            await self._auto_task_record_step(result.tool_calls, tool_results)

            # Check for finalized flag AFTER dispatching — response_publish
            # already published the content, but other tools in the batch
            # must still execute their side effects.
            session = get_session_optional(self.ctx.visitor)
            if session is not None and session.finalized:
                await self._auto_task_finalize(
                    success=True,
                    result_summary="response_publish(finalize=true) called",
                )
                return CockpitStepResult(
                    status="final_response",
                    final_response="",  # Content already published via response_publish
                    termination_reason=TerminationReason.COMPLETED,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            # All-errors short-circuit: if every tool call failed, don't loop
            if tool_results and all(
                getattr(tr, "is_error", False) for tr in tool_results
            ):
                error_details = "\n".join(
                    f"- {tc.get('function', {}).get('name', '?')}: {getattr(tr, 'content', '')[:200]}"
                    for tc, tr in zip(result.tool_calls, tool_results)
                )
                # Emit the per-tool error trace as a transient thought so
                # operators / observability see the failure detail. The
                # user-facing reply stays generic — tool-error language
                # belongs in thoughts, never in a chat bubble.
                await self._emit_tool_error_thought(error_details)
                await self._auto_task_finalize(
                    success=False,
                    result_summary="all tool calls in batch errored",
                    reason="all_errors",
                )
                return CockpitStepResult(
                    status="final_response",
                    final_response=(
                        "Sorry — I ran into an issue completing that. "
                        "Could you rephrase or try again?"
                    ),
                    termination_reason=TerminationReason.ERROR,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            # Staging short-circuit. When every tool call in this
            # iteration is a ``prepare_*`` skill (the cockpit's
            # confirmation-card pattern), the staged-change card IS
            # the user-facing response — there is nothing useful for
            # the model to add in a follow-up text turn. Re-prompting
            # the model produces one of three failure modes:
            #   1. A truncated junk fragment (we observed gpt-5-mini
            #      emitting "OnSt" — the model began "On staging…"
            #      then stopped, leaving 4 chars of garbage in the
            #      chat history).
            #   2. A redundant "Filed X in Y" prose line that
            #      duplicates what the StagedChangeCard's deterministic
            #      consumed-state confirmation will say.
            #   3. A "Let me know if you need anything else" closer
            #      that violates the persona's "composer is the
            #      implicit invitation" rule.
            # Returning ``final_response=""`` here ends the turn
            # cleanly — no extra model round-trip, no risk of stray
            # text. The staging cards (rendered inline by the FE's
            # ``StagedChangeToolUI``) already carry the full
            # information the user needs.
            #
            # ``prepare_*`` is the established cockpit naming
            # convention — checked against the bare tool name
            # (after the ``namespace__`` prefix) so any namespace
            # works (``integral_filing__prepare_file_content`` →
            # bare ``prepare_file_content``).
            def _is_prepare_call(tc: Dict[str, Any]) -> bool:
                fn = tc.get("function") if isinstance(tc, dict) else None
                name = (fn or {}).get("name", "") if isinstance(fn, dict) else ""
                bare = str(name or "").split("__")[-1]
                return bare.startswith("prepare_")

            if result.tool_calls and all(
                _is_prepare_call(tc) for tc in result.tool_calls
            ):
                await self._auto_task_finalize(
                    success=True,
                    result_summary="prepare_* tool calls; card carries the response",
                )
                return CockpitStepResult(
                    status="final_response",
                    final_response="",
                    termination_reason=TerminationReason.COMPLETED,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            # Stuck detection
            if self._check_stuck():
                await self._auto_task_finalize(
                    success=False,
                    result_summary="stuck detection fired",
                    reason="stuck",
                )
                return CockpitStepResult(
                    status="stuck",
                    final_response=(
                        "I seem to be making the same actions repeatedly without progress. "
                        "Let me try a different approach."
                    ),
                    termination_reason=TerminationReason.STUCK,
                    iterations=self._iteration,
                    duration_seconds=time.monotonic() - self._start,
                    activated_skills=list(self._activated_skills),
                )

            if cfg.stream_internal_progress and self.ctx.stream:
                await self._emit_tool_progress(
                    result.tool_calls, tool_results, self._iteration
                )

            return CockpitStepResult(
                status="tool_calls",
                iterations=self._iteration,
                duration_seconds=time.monotonic() - self._start,
                activated_skills=list(self._activated_skills),
            )

        # No tool calls — model produced a final text response
        response_text = await result.get_response()
        logger.debug(
            "CockpitEngine [%d]: model produced final response (%d chars)",
            self._iteration,
            len(response_text),
        )
        # Auto-task finalize on natural completion. Summarise with the first
        # ~120 chars of the final response to preserve grep-ability without
        # bloating the task data bag.
        summary = (response_text or "")[:120].replace("\n", " ").strip()
        await self._auto_task_finalize(
            success=True,
            result_summary=summary or "completed",
        )
        return CockpitStepResult(
            status="final_response",
            final_response=response_text,
            termination_reason=TerminationReason.COMPLETED,
            iterations=self._iteration,
            duration_seconds=time.monotonic() - self._start,
            activated_skills=list(self._activated_skills),
        )

    def _model_query_kwargs(self) -> Dict[str, Any]:
        """Build per-call kwargs for ``model_action.query_messages``.

        Forwards the cockpit's engine-model knobs (``model``,
        ``model_temperature``, ``model_max_tokens``) plus the provider-specific
        reasoning translation, so operator settings on the cockpit override
        the underlying model action's defaults rather than being silently
        ignored.
        """
        from jvagent.action.model.language.base import ReasoningModelConfig

        cfg = self.ctx.config
        kwargs: Dict[str, Any] = {
            "temperature": cfg.model_temperature,
            "max_tokens": cfg.model_max_tokens,
        }
        if cfg.model:
            kwargs["model"] = cfg.model

        reasoning_cfg = ReasoningModelConfig(
            reasoning_effort=cfg.reasoning_effort,
            reasoning_budget_tokens=cfg.reasoning_budget_tokens,
            reasoning_enabled=cfg.reasoning_enabled,
            reasoning_extra=cfg.reasoning_extra,
        )
        translate = getattr(self.ctx.model_action, "translate_reasoning_config", None)
        if callable(translate):
            try:
                translated = translate(reasoning_cfg)
                if isinstance(translated, dict):
                    kwargs.update(translated)
            except Exception as exc:
                logger.debug(
                    "translate_reasoning_config failed (%s); skipping reasoning kwargs",
                    type(exc).__name__,
                )
        return kwargs

    def save_state(self) -> CockpitState:
        """Capture engine state for observability/debugging.

        The engine instance is persisted across walker visits via
        ``CockpitSession.engine`` on the visitor, so state restoration
        is handled by reusing the same engine rather than deserializing.
        """
        return CockpitState(
            messages=list(self._messages),
            iteration=self._iteration,
            activated_skills=list(self._activated_skills),
            started_at=self._start,
            tools_serialized=list(self._tools_serialized),
            recent_tool_names=[list(names) for names in self._recent_tool_names],
            recent_tool_signatures=[
                list(sigs) for sigs in self._recent_tool_signatures
            ],
        )

    def _check_stuck(self) -> bool:
        """Return True if recent tool calls show repetitive patterns.

        Two checks (both gated by ``stuck_min_iterations`` to avoid false
        positives during the early iterations of a legitimate multi-step plan):

        1. Jaccard similarity on tool-call **signature** sets (name + args
           fingerprint) across a sliding window. Calling the same tool with
           progressively different arguments is NOT stuck — it's refinement.
        2. Same primary tool **signature** repeated N consecutive times — i.e.
           identical name+args, not just same name.
        """
        cfg = self.ctx.config
        window_size = getattr(cfg, "stuck_detection_window", 4)
        threshold = getattr(cfg, "stuck_intent_jaccard_threshold", 0.65)
        repeat_limit = getattr(cfg, "stuck_primary_tool_repeat", 4)
        min_iters = getattr(cfg, "stuck_min_iterations", 4)

        # Don't engage stuck detection until the model has had time to work.
        if len(self._recent_tool_signatures) < min_iters:
            return False

        # Check A: Jaccard similarity on tool-call signature sets.
        if len(self._recent_tool_signatures) >= window_size:
            window = self._recent_tool_signatures[-window_size:]
            sets = [set(sigs) for sigs in window if sigs]
            if len(sets) >= 2:
                all_similar = True
                for i in range(len(sets) - 1):
                    intersection = len(sets[i] & sets[i + 1])
                    union = len(sets[i] | sets[i + 1])
                    if union == 0:
                        continue
                    jaccard = intersection / union
                    if jaccard < threshold:
                        all_similar = False
                        break
                if all_similar:
                    return True

        # Check B: Same primary tool signature repeated N consecutive times.
        if len(self._recent_tool_signatures) >= repeat_limit:
            recent = self._recent_tool_signatures[-repeat_limit:]
            primary_sigs = [sigs[0] if sigs else "" for sigs in recent]
            if len(set(primary_sigs)) == 1 and primary_sigs[0]:
                return True

        return False

    async def _build_system_prompt(self) -> str:
        skill_index = ""
        task_planning = ""
        capability_search_note = ""
        user_memory = ""
        security_block = ""
        current_datetime = ""
        user_identity = ""

        skill_state = getattr(self.ctx.visitor, "_skill_state", None) or {}
        catalog = skill_state.get("skill_catalog")
        cfg = self.ctx.config

        large_catalog = bool(
            catalog and len(catalog.skills) > cfg.skill_index_inline_max_skills
        )

        if catalog and self.ctx.preloaded_skills:
            filtered = {
                k: v
                for k, v in catalog.skills.items()
                if k in self.ctx.preloaded_skills
            }
            if filtered:
                try:
                    from jvagent.action.cockpit.catalog.skill_catalog import (
                        SkillCatalog,
                    )

                    sub = SkillCatalog(filtered)

                    sop_sections: List[str] = []
                    for skill_name, data in filtered.items():
                        content = (data.get("content") or "").strip()
                        description = (data.get("description") or "").strip()
                        if not content and not description:
                            continue
                        section = [f"## Skill: {skill_name}"]
                        if description:
                            section.append(f"**Description:** {description}")
                        if content:
                            section.append(content)
                        allowed = data.get("allowed_tools", []) or []
                        if allowed:
                            section.append(f"**Available tools:** {', '.join(allowed)}")
                        sop_sections.append("\n\n".join(section))

                    if sop_sections:
                        skill_names = ", ".join(filtered.keys())
                        # Build peer-skill quick index so the model can pivot
                        # without calling skill_search when the recommendation
                        # is a poor fit.
                        peer_lines: List[str] = []
                        for s_name, s_data in catalog.skills.items():
                            if s_name in filtered:
                                continue
                            desc = (s_data.get("description") or "").strip()
                            short = " ".join(desc.split())[:200]
                            peer_lines.append(f"- **{s_name}** — {short}")
                        peer_index = (
                            "\n\n**Other skills available** (call `skill_read` "
                            "to load full SOP if any of these fits better):\n"
                            + "\n".join(peer_lines)
                            if peer_lines
                            else ""
                        )
                        skill_index = (
                            "\n\n# Router-selected skill(s) — SOP pre-loaded\n"
                            f"The router classified this request and selected: **{skill_names}**. "
                            "The full SOP is inlined below.\n\n"
                            "**Engagement rule (course-correction expected):**\n"
                            "- If the SOP fits the user's actual request, proceed "
                            "directly to its tools and workflow. Do NOT call "
                            "`skill_read` for the listed skill(s).\n"
                            "- If you judge the recommendation is a wrong fit, "
                            "treat it as a router miss: pick a better skill from "
                            "the **Other skills available** list below, call "
                            "`skill_read` on it, and use its tools. Course-"
                            "correction is expected when the recommendation "
                            "doesn't match — the router is fast but fallible.\n"
                            "- Do NOT answer from your own world knowledge while "
                            "any catalog skill could plausibly satisfy the "
                            "request. World-knowledge replies are only "
                            "acceptable when no listed skill fits.\n\n"
                            + "\n\n---\n\n".join(sop_sections)
                            + peer_index
                        )
                    else:
                        skill_index = (
                            "\n\n# Available skills\n"
                            + sub.render_catalog()
                            + "\n\nUse skill_read with the exact skill name before activating any skill."
                            + " Do NOT answer from your own world knowledge before"
                            " consulting the catalog."
                        )
                except Exception:
                    pass
        elif catalog:
            n = len(catalog.skills)
            if n <= cfg.skill_index_inline_max_skills:
                try:
                    skill_index = (
                        "\n\n# Available skills\n"
                        + catalog.render_catalog()
                        + "\n\nUse skill_read with the exact skill name before activating any skill."
                        + " Do NOT answer from your own world knowledge before"
                        " consulting the catalog."
                    )
                except Exception:
                    pass
            elif cfg.enable_skill_helper_tools:
                skill_index = (
                    "\n\n"
                    + "You have access to multiple Claude-style skill bundles. "
                    + "Use skill_search with keywords from the user's request, "
                    + "then call skill_read to load the full instructions. "
                    + "Do NOT answer from your own world knowledge before "
                    + "consulting the catalog."
                )

        # Advertise cockpit_search prominently when the catalog is large or the
        # agent has many action tools — these are the cases where listing
        # everything inline isn't viable.
        if cfg.enable_cockpit_search and large_catalog:
            capability_search_note = (
                getattr(cfg, "capability_search_prompt", "") or CAPABILITY_SEARCH_NOTE
            )

        if cfg.plan_first:
            task_planning = (
                getattr(cfg, "task_planning_prompt", "") or TASK_PLANNING_BLOCK
            )

        # Phase B: pre-load user-scoped memory into the system prompt so the
        # model has stable context about the human without spending a tool call.
        if getattr(cfg, "preload_user_memory", True):
            try:
                from jvagent.action.cockpit.tools.memory import render_user_memory_block

                block = await render_user_memory_block(
                    self.ctx,
                    max_chars=getattr(cfg, "user_memory_max_chars", 4096),
                )
                if block:
                    user_memory = "\n\n" + block + "\n"
            except Exception as exc:
                logger.debug("user memory preload failed: %s", exc)

        if getattr(cfg, "block_raw_tool_invocation", False):
            security_block = getattr(cfg, "security_prompt", "") or SECURITY_BLOCK

        # Inject current date / time / timezone so the model has a temporal
        # anchor without needing to call ``get_current_datetime`` for every
        # trivial reference. The tool remains the source of truth for any
        # arithmetic, alternate timezones, or up-to-the-second precision.
        try:
            from jvagent.action.cockpit.tools.clock import (
                _format_datetime_block,
                _resolve_now,
            )

            now = await _resolve_now()
            current_datetime = (
                "\n\n# Current date / time (your point of reference for 'now')\n"
                + _format_datetime_block(now)
                + "\nFor alternate timezones or post-anchor precision, "
                "call ``get_current_datetime``."
            )
        except Exception as exc:
            logger.debug("current-datetime preload failed: %s", exc)

        # Inject the caller's preferred name so the model addresses the
        # user correctly without calling ``get_user_name`` first. Falls
        # through to a "no name on file — ask the user" stub when the
        # User node has neither display_name nor name.
        try:
            user_identity = await self._render_user_identity_block()
        except Exception as exc:
            logger.debug("user-identity preload failed: %s", exc)

        system_prompt_template = (
            getattr(cfg, "system_prompt", "") or COCKPIT_SYSTEM_PROMPT
        )
        return system_prompt_template.format(
            agent_name=self.ctx.agent_name,
            agent_description=self.ctx.agent_description,
            skill_index=skill_index,
            task_planning=task_planning,
            capability_search_note=capability_search_note,
            user_memory=user_memory,
            security_block=security_block,
            current_datetime=current_datetime,
            user_identity=user_identity,
        )

    async def _render_user_identity_block(self) -> str:
        """Resolve caller name from the User node and render a prompt block.

        Returns an empty string when no user_id / agent / memory subsystem
        is available (test setups, anonymous flows). The full ``respond()``
        path mirrors this contract via ``PersonaAction._render_user_context_block``.
        """
        user_id = getattr(self.ctx, "user_id", None)
        agent = getattr(self.ctx, "agent", None)
        if not user_id or agent is None:
            return ""
        try:
            memory = await agent.get_memory()
            user = await memory.get_user(user_id) if memory is not None else None
        except Exception as exc:
            logger.debug("user-identity lookup failed: %s", exc)
            return ""

        display_name = ""
        canonical_name = ""
        if user is not None:
            try:
                display_name = (getattr(user, "display_name", "") or "").strip()
            except Exception:
                display_name = ""
            try:
                canonical_name = (getattr(user, "name", "") or "").strip()
            except Exception:
                canonical_name = ""

        chosen = display_name or canonical_name
        if chosen:
            extra = ""
            if display_name and canonical_name and display_name != canonical_name:
                extra = f"\nCanonical name: {canonical_name}"
            return (
                "\n\n# Current user (your authoritative reference for who you are speaking to)\n"
                f"Preferred name: {chosen}"
                f"{extra}\n"
                "Address the user by this name. Never invent or alter it. "
                "If the user offers a different name, persist via "
                "``memory_update_user_model`` (key=``name``)."
            )
        return (
            "\n\n# Current user\n"
            "No name is on file for this user. If a greeting needs a "
            "name, ask politely how they would like to be addressed; "
            "persist the answer via ``memory_update_user_model`` "
            "(key=``name``). Never invent a name."
        )

    async def _maybe_pre_dispatch(self) -> None:
        """Auto-invoke each preloaded skill's declared dispatch tool.

        When the router pre-selects a skill whose frontmatter declares a
        ``dispatch`` block, run that tool synchronously and inject the
        resulting ``assistant(tool_calls)`` + ``tool(result)`` pair into the
        message history. This converts a soft hint ("recommended skill: X")
        into a structural fait accompli — the model's first ``step()`` sees
        real tool output and synthesizes directly, with no opportunity to
        skip the skill, freelance, or hallucinate.

        Silently no-ops when:
          - the router returned ≠1 skills (single-skill routes only — see
            below);
          - the catalog is missing, no preloaded skills, or none declare
            ``dispatch``;
          - the dispatch tool name is not registered (router routed to a
            skill whose tools aren't bound for this run);
          - the executor isn't ready (defensive — initialize() ordering).

        Multi-skill gating: when the router returns more than one skill it
        is hedging across capabilities; pre-dispatching one of them risks
        running a tool that doesn't match the user's actual intent (e.g.
        firing ``pageindex__search`` for a product-catalog request when the
        router routed ``[pageindex_search, product_recommendations]``). In
        those cases the standard model-driven loop is the correct arbiter.
        Always-active skills appearing in ``preloaded_skills`` do not count
        toward this gate; only direct router selections do.
        """
        routed = list(getattr(self.ctx, "routed_skills", []) or [])
        if len(routed) != 1:
            return
        executor = self._tool_executor
        registry = self._registry
        if executor is None or registry is None:
            return

        skill_state = getattr(self.ctx.visitor, "_skill_state", None) or {}
        catalog = skill_state.get("skill_catalog")
        if catalog is None:
            return
        skills = getattr(catalog, "skills", {}) or {}

        utterance = (self.ctx.utterance or "").strip()
        interpretation = ""
        interaction = getattr(self.ctx, "interaction", None)
        if interaction is not None:
            interpretation = (getattr(interaction, "interpretation", "") or "").strip()

        registered_names = set(registry.names())

        for skill_name in routed:
            data = skills.get(skill_name)
            if not data:
                continue
            dispatch = data.get("dispatch")
            # Implicit-dispatch fallback: when a skill declares no
            # ``dispatch:`` block but exposes exactly ONE entry under
            # ``allowed-tools``, infer dispatch from that sole tool. Avoids
            # forcing every retrieval-style skill to repeat the tool name
            # in two places. Skills with multiple allowed tools (or none)
            # do not auto-dispatch — author must declare ``dispatch:``
            # explicitly to opt in.
            if not isinstance(dispatch, dict):
                allowed = data.get("allowed_tools") or []
                if isinstance(allowed, list) and len(allowed) == 1:
                    inferred = str(allowed[0]).strip()
                    if inferred:
                        dispatch = {
                            "tool": inferred,
                            "arg": "query",
                            "source": "utterance",
                            "extra": {},
                        }
                        logger.debug(
                            "Pre-dispatch: inferred dispatch for skill %s "
                            "from sole allowed-tools entry %r",
                            skill_name,
                            inferred,
                        )
                if not isinstance(dispatch, dict):
                    continue
            tool_name = str(dispatch.get("tool") or "").strip()
            if not tool_name or tool_name not in registered_names:
                if tool_name:
                    logger.debug(
                        "Pre-dispatch: skill %s declared tool %s but it is "
                        "not registered for this run; skipping",
                        skill_name,
                        tool_name,
                    )
                continue
            arg_name = str(dispatch.get("arg") or "query").strip() or "query"
            source = str(dispatch.get("source") or "utterance").strip().lower()
            value = interpretation if source == "interpretation" else utterance
            if not value:
                value = utterance or interpretation
            if not value:
                continue

            args: Dict[str, Any] = {arg_name: value}
            extra = dispatch.get("extra")
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if k and k not in args:
                        args[k] = v

            import json as _json
            import uuid as _uuid

            tool_call_id = f"call_predispatch_{_uuid.uuid4().hex[:12]}"
            tool_call = {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": _json.dumps(args, ensure_ascii=False),
                },
            }

            try:
                tool_results = await executor.dispatch([tool_call])
            except Exception as exc:
                logger.warning(
                    "Pre-dispatch %s for skill %s raised: %s",
                    tool_name,
                    skill_name,
                    exc,
                )
                continue

            if not tool_results:
                continue
            tr = tool_results[0]
            if getattr(tr, "is_error", False):
                logger.debug(
                    "Pre-dispatch %s for skill %s returned error; skipping injection",
                    tool_name,
                    skill_name,
                )
                continue

            self._messages.append(
                {"role": "assistant", "content": None, "tool_calls": [tool_call]}
            )
            self._messages.append(tr.tool_result_message())

            self._recent_tool_names.append([tool_name])
            self._recent_tool_signatures.append([_tool_call_signature(tool_call)])

            await self._auto_task_record_step([tool_call], tool_results)

            logger.info(
                "Pre-dispatch: ran %s for skill %s (utterance=%r, %d chars result)",
                tool_name,
                skill_name,
                value[:60],
                len(getattr(tr, "content", "") or ""),
            )

    async def _build_history(self) -> List[Dict[str, Any]]:
        """Build the prior-turn history that primes the engine's message list.

        Uses ``formatted=True`` so the conversation node returns ``{role,
        content}`` pairs ready for the model. (``formatted=False`` returns
        ``{interaction_id, utterance, response}`` instead — that shape was
        silently dropping every entry here when read as role/content.)
        """
        if not self.ctx.conversation or self.ctx.config.history_limit <= 0:
            return []

        try:
            raw = await self.ctx.conversation.get_interaction_history(
                limit=self.ctx.config.history_limit,
                excluded=(
                    self.ctx.interaction.id
                    if (self.ctx.interaction and self.ctx.interaction.id)
                    else None
                ),
                with_utterance=True,
                with_response=True,
                formatted=True,
                max_statement_length=self.ctx.config.max_statement_length,
            )
            messages: List[Dict[str, Any]] = []
            for entry in raw or []:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                if role and content:
                    messages.append({"role": role, "content": content})
            return messages
        except Exception as exc:
            logger.debug("CockpitEngine._build_history failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Auto-task tracking
    # ------------------------------------------------------------------

    async def _auto_task_start(self) -> None:
        """Create the trace task at the start of a cockpit run.

        The trace task is shared with the model: if it calls
        ``task_create_plan`` / ``task_update_step`` etc., those tools resolve
        to this same task (via ``CockpitSession.trace_task_id`` on the visitor).
        Once the model has planned, ``_auto_task_record_step`` stops appending
        iteration steps so the model's plan steps drive the trace.
        """
        try:
            store = getattr(self.ctx.visitor, "tasks", None)
            if store is None:
                return
            utterance = (self.ctx.utterance or "").strip()
            title = (
                (utterance[:80] + "…")
                if len(utterance) > 80
                else (utterance or "Cockpit run")
            )
            task = await store.create(
                title=title,
                description=utterance or "Cockpit auto-tracked run",
                owner_action="CockpitInteractAction",
            )
            await task.start()
            self._trace_task = task
            # Expose the task ID so model-facing task tools can resolve to it.
            session = get_session(self.ctx.visitor)
            session.trace_task_id = getattr(task, "id", None)
            session.model_planned = False
        except Exception as exc:
            logger.debug("auto-task start failed: %s", exc)
            self._trace_task = None

    async def _auto_task_record_step(
        self,
        tool_calls: List[Dict[str, Any]],
        tool_results: List[Any],
    ) -> None:
        """Record this iteration's tool calls on the trace task.

        Two modes:

        - **Auto mode (no model plan).** Append a new step to the trace task with
          a human-readable description (truncated tool-call list) and a
          structured ``data`` bag holding the full tool-call detail (name,
          arguments, result preview, error state, tool_call_id). The step is
          marked ``done`` if all tool calls succeeded, ``failed`` otherwise.

        - **Plan mode (model has planned).** Find the currently in-progress
          model step and attach the iteration as a ``tool_calls`` sub-event on
          its ``_events`` list. This keeps the model's intentional plan steps
          clean while preserving full tool-call observability under each step.
          If no step is in-progress (model planned but hasn't called
          ``task_update_step(in_progress)`` yet), fall back to appending a
          fresh ``engine_trace`` step so observability is never lost.
        """
        if self._trace_task is None:
            return
        try:
            tool_details = self._build_tool_details(tool_calls, tool_results)
            ok = sum(1 for t in tool_details if not t["is_error"])
            errored = len(tool_details) - ok
            summary = (
                f"{ok}/{len(tool_details)} ok"
                + (f", {errored} errored" if errored > 0 else "")
                if tool_details
                else "no tool results"
            )
            description = self._iteration_description(tool_details)

            session = get_session_optional(self.ctx.visitor)
            model_planned = bool(session.model_planned) if session else False

            # Plan mode → attach as a sub-event on the active model step
            # (preserves model's clean plan steps; full detail under _events).
            if model_planned:
                active = self._find_in_progress_step()
                if active is not None:
                    await active.add_event(
                        event_type="tool_calls",
                        iteration=self._iteration,
                        details={
                            "summary": summary,
                            "tool_calls": tool_details,
                        },
                    )
                    return

            # Auto mode (or plan mode with no active step) → append a step.
            step = await self._trace_task.add_step(
                description,
                data={
                    "iteration": self._iteration,
                    "source": "engine_trace",
                    "tool_calls": tool_details,
                    "summary": {
                        "ok": ok,
                        "errored": errored,
                        "total": len(tool_details),
                    },
                },
            )
            await step.start()
            if errored > 0:
                await step.fail(reason=summary)
            else:
                await step.complete(result=summary)
        except Exception as exc:
            logger.debug("auto-task record step failed: %s", exc)

    def _find_in_progress_step(self) -> Any:
        """Return the first in_progress StepHandle on the trace task, or None."""
        if self._trace_task is None:
            return None
        try:
            for step in self._trace_task.list_steps(status="in_progress"):
                return step
        except Exception:
            pass
        return None

    @staticmethod
    def _build_tool_details(
        tool_calls: List[Dict[str, Any]], tool_results: List[Any]
    ) -> List[Dict[str, Any]]:
        """Build structured tool-call detail for the step data bag.

        Captures (name, arguments, result_preview, result_length, is_error,
        tool_call_id) for each call. Arguments are kept full (they are part of
        the observable trace); result preview is capped at 500 chars with
        result_length recording the original size.
        """
        import json as _json

        out: List[Dict[str, Any]] = []
        for tc, tr in zip(tool_calls, tool_results):
            fn = (tc or {}).get("function") or {}
            name = fn.get("name") or "?"
            args_raw = fn.get("arguments")
            if isinstance(args_raw, dict):
                try:
                    args_str = _json.dumps(args_raw, default=str)
                except Exception:
                    args_str = str(args_raw)
            else:
                args_str = str(args_raw or "")
            result_content = str(getattr(tr, "content", "") or "")
            preview = (
                result_content[:500] + "…"
                if len(result_content) > 500
                else result_content
            )
            out.append(
                {
                    "tool_call_id": (tc or {}).get("id"),
                    "name": name,
                    "arguments": args_str,
                    "result_preview": preview,
                    "result_length": len(result_content),
                    "is_error": bool(getattr(tr, "is_error", False)),
                }
            )
        return out

    def _iteration_description(self, tool_details: List[Dict[str, Any]]) -> str:
        """Build a scannable description: ``iter N: tool1(args); tool2(args); …``."""
        parts: List[str] = []
        for d in tool_details[:4]:
            args = d["arguments"]
            if len(args) > 60:
                args = args[:57] + "…"
            parts.append(f"{d['name']}({args})")
        if len(tool_details) > 4:
            parts.append(f"+{len(tool_details) - 4} more")
        if not parts:
            return f"iter {self._iteration}"
        return f"iter {self._iteration}: " + "; ".join(parts)

    async def _auto_task_finalize(
        self,
        *,
        success: bool,
        result_summary: str,
        reason: str = "",
    ) -> None:
        """Close out the trace task on terminal step result."""
        if self._trace_task is None:
            return
        try:
            if success:
                await self._trace_task.complete(result=result_summary or None)
            else:
                await self._trace_task.fail(reason=reason or result_summary or None)
        except Exception as exc:
            logger.debug("auto-task finalize failed: %s", exc)

    async def _emit_tool_error_thought(self, error_details: str) -> None:
        """Publish a per-tool error trace as a transient thought.

        Used by the all-errors short-circuit so the operator-facing detail
        is captured on the response bus without leaking tool internals into
        the user's chat reply.

        When ``sanitize_tool_errors`` is True (default), the raw per-tool
        ``content`` is NOT included in the streamed thought — only the tool
        names. Many harness tools return ``f"Error: {exc}"`` directly, which
        previously flowed through this publish path despite the sanitize
        flag (AUDIT-interact-cockpit CRIT-05). The full error is still
        recorded to the standard logger for operators / DBLogHandler.
        """
        ctx = self.ctx
        if not getattr(ctx.config, "stream_internal_progress", True):
            # Still log for ops even when streaming is disabled.
            logger.warning(
                "Cockpit tool batch failed",
                extra={"details": {"error_details": error_details or ""}},
            )
            return
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        body = (error_details or "").strip()
        if not body:
            return

        sanitize = bool(getattr(ctx.config, "sanitize_tool_errors", True))
        if sanitize:
            # Always log the raw detail for ops; strip it from the streamed thought.
            logger.warning(
                "Cockpit tool batch failed (full detail logged; stream sanitized)",
                extra={"details": {"error_details": body}},
            )
            sanitized_lines: list[str] = []
            for raw_line in body.split("\n"):
                # Each line begins with ``- {tool_name}: {content[:200]}``.
                # Keep ``- {tool_name}: error`` only.
                stripped = raw_line.lstrip()
                if stripped.startswith("- ") and ":" in stripped[2:]:
                    name_part = stripped[2:].split(":", 1)[0].strip()
                    sanitized_lines.append(f"- {name_part}: error")
                else:
                    sanitized_lines.append("- error")
            streamed_body = "\n".join(sanitized_lines)
        else:
            streamed_body = body

        await ctx.response_bus.publish(
            session_id=ctx.session_id,
            content=f"Tool batch failed:\n{streamed_body}",
            channel=ctx.channel,
            stream=ctx.stream,
            interaction_id=ctx.interaction.id,
            interaction=ctx.interaction,
            user_id=ctx.user_id,
            streaming_complete=True,
            transient=True,
            category="thought",
            thought_type="tool_error",
        )

    async def _consume_stream_with_live_reasoning(self, result: Any) -> None:
        """Drain a streaming result while emitting reasoning deltas live.

        Runs two concurrent consumers:
        1. ``result.stream`` — text chunks accumulated into ``result.response``;
           tool_calls are populated on the result by the provider when the
           stream finishes.
        2. ``result.iter_thinking()`` — reasoning deltas published to the
           response bus as they arrive, giving the UI real-time visibility
           into the model's chain of thought.

        After both complete the result object is fully populated (response,
        tool_calls, thinking_content, metrics) and the caller can proceed
        exactly as if ``stream=False`` had been used.
        """
        ctx = self.ctx
        cfg = ctx.config
        can_emit = (
            getattr(cfg, "stream_internal_progress", True)
            and ctx.response_bus
            and ctx.session_id
            and ctx.interaction
        )

        async def _drain_text() -> None:
            stream = getattr(result, "stream", None)
            if not stream:
                return
            chunks: list[str] = []
            async for chunk in stream:
                chunks.append(chunk)
            if chunks and not getattr(result, "response", None):
                result.response = "".join(chunks)

        async def _drain_thinking() -> None:
            if not can_emit:
                return
            iter_thinking = getattr(result, "iter_thinking", None)
            if not iter_thinking:
                return
            seg_id = f"reasoning-{self._iteration}"
            async for delta in iter_thinking():
                await ctx.response_bus.publish(
                    session_id=ctx.session_id,
                    content=delta,
                    channel=ctx.channel,
                    stream=ctx.stream,
                    interaction_id=ctx.interaction.id,
                    interaction=ctx.interaction,
                    user_id=ctx.user_id,
                    segment_id=seg_id,
                    streaming_complete=False,
                    transient=True,
                    category="thought",
                    thought_type="reasoning",
                )

        await asyncio.gather(_drain_text(), _drain_thinking())

    async def _emit_thinking_thought(self, result: Any) -> None:
        """Publish the model's thinking/reasoning text as a single thought.

        Fallback for non-streaming queries. When streaming is active,
        ``_consume_stream_with_live_reasoning`` handles reasoning deltas
        incrementally instead.
        """
        ctx = self.ctx
        cfg = ctx.config
        if not getattr(cfg, "stream_internal_progress", True):
            return
        thinking = getattr(result, "thinking_content", None)
        if not thinking or not isinstance(thinking, str) or not thinking.strip():
            return
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        await ctx.response_bus.publish(
            session_id=ctx.session_id,
            content=thinking.strip(),
            channel=ctx.channel,
            stream=ctx.stream,
            interaction_id=ctx.interaction.id,
            interaction=ctx.interaction,
            user_id=ctx.user_id,
            streaming_complete=True,
            transient=True,
            category="thought",
            thought_type="reasoning",
        )

    async def _emit_tool_progress(
        self,
        tool_calls: List[Dict[str, Any]],
        results: List[Any],
        iteration: int,
    ) -> None:
        ctx = self.ctx
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        for tc, tr in zip(tool_calls, results):
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            tool_call_id = tc.get("id") or ""
            # Suppress for user-visible tools (response_publish, etc.).
            if _suppress_tool_observability(tool_name):
                continue
            status = (
                "failed"
                if (
                    getattr(tr, "is_error", False)
                    or (
                        isinstance(tr, dict)
                        and (tr.get("content") or "").startswith("Error:")
                    )
                )
                else "ok"
            )
            content = f"[{status}] {tool_name}"
            await ctx.response_bus.publish(
                session_id=ctx.session_id,
                content=content,
                channel=ctx.channel,
                stream=ctx.stream,
                interaction_id=ctx.interaction.id,
                interaction=ctx.interaction,
                user_id=ctx.user_id,
                streaming_complete=True,
                transient=True,
                category="thought",
                thought_type="tool_progress",
                # Use the SAME segment_id pattern as _emit_tool_call /
                # _emit_tool_result so downstream consumers can dedupe
                # this human-readable summary against the structured
                # envelopes for the same call. Without this the bus
                # auto-generates a random segment_id and consumers
                # double-count the same call.
                segment_id=tool_call_id or f"iter{iteration}-{tool_name}",
            )

    # ------------------------------------------------------------------
    # Structured tool envelopes (Integral SPEC §7.3 #1)
    #
    # ``_emit_tool_progress`` above is a HUMAN-READABLE post-hoc
    # summary ("[ok] tool_name"). Downstream consumers that want the
    # actual args + results (so they can render rich tool UIs, count
    # tokens per tool, audit calls, etc.) need structured envelopes
    # instead. The two methods below provide them:
    #
    #   ``_emit_tool_call``   — published BEFORE dispatch. Carries the
    #     tool's name, its id (OpenAI tool_call_id, used to pair with
    #     the matching result), and the parsed args dict. Lets
    #     consumers render "Calling X with Y" the moment the model
    #     decides — no waiting for execution.
    #
    #   ``_emit_tool_result`` — published AFTER dispatch. Carries the
    #     same id (so consumers can match it with the prior call),
    #     plus the actual return value and an is_error flag.
    #
    # Both are gated on the same ``stream_internal_progress`` flag as
    # ``_emit_tool_progress`` and are ADDITIVE — existing consumers
    # of ``tool_progress`` keep working unchanged. Keeping all three
    # is intentional: ``tool_progress`` is the cheapest one-line
    # summary for log scrapers; the structured pair is for rich UIs.
    # ------------------------------------------------------------------

    async def _emit_tool_call(
        self,
        tool_calls: List[Dict[str, Any]],
        iteration: int,
    ) -> None:
        """Publish one ``thought_type=tool_call`` envelope per planned
        tool, BEFORE dispatch. Lets the FE render an inline
        "calling X" affordance the moment the model decides.

        Structured payload travels in the message's ``metadata`` dict
        (see ``ResponseMessage.to_dict`` — it surfaces ``metadata``
        in the SSE envelope). The ``segment_id`` is set to the
        OpenAI tool_call_id so the matching ``tool_result`` envelope
        can be paired by the consumer without ambiguity.
        """
        ctx = self.ctx
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name") or "unknown"
            tool_call_id = tc.get("id") or ""
            # Suppress observability emit for tools whose effect is
            # already user-visible (response_publish, etc.). Avoids
            # the noisy ``← ok: response_publish`` line in jvchat's
            # Reasoning panel.
            if _suppress_tool_observability(tool_name):
                continue
            # Idempotency: skip if we've already published this
            # tool_call envelope. Guards against any upstream cause
            # of duplicate emission — walker re-walks, parallel
            # tasks holding stale tool_call lists, etc. Each (id,
            # kind) pair produces exactly one wire envelope.
            dedupe_key = (tool_call_id, "tool_call")
            if dedupe_key in self._emitted_envelopes:
                continue
            self._emitted_envelopes.add(dedupe_key)
            # Args arrive from OpenAI as a JSON string; decode for
            # downstream consumers so they don't have to parse.
            raw_args = fn.get("arguments")
            try:
                tool_args = (
                    json.loads(raw_args)
                    if isinstance(raw_args, str) and raw_args
                    else (raw_args if isinstance(raw_args, dict) else {})
                )
            except (json.JSONDecodeError, TypeError):
                # Fall back to raw string if it isn't valid JSON —
                # the consumer can still display it.
                tool_args = {"_raw": raw_args}
            await ctx.response_bus.publish(
                session_id=ctx.session_id,
                # ``content`` is a human-readable line so log scrapers
                # see something useful; the structured payload rides
                # in ``metadata`` (surfaced via ResponseMessage.to_dict).
                content=f"calling {tool_name}",
                channel=ctx.channel,
                # stream=False — these envelopes are DISCRETE, not
                # streaming text. Under stream=True the response_bus
                # chunks the content string into many SSE events per
                # publish (we hit this pathology earlier with
                # tool_progress). One emit → one event.
                stream=False,
                interaction_id=ctx.interaction.id,
                interaction=ctx.interaction,
                user_id=ctx.user_id,
                streaming_complete=True,
                transient=True,
                category="thought",
                thought_type="tool_call",
                # ``segment_id`` lets the downstream consumer pair
                # this call with its matching ``tool_result`` event.
                segment_id=tool_call_id or f"iter{iteration}-{tool_name}",
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "iteration": iteration,
                },
            )

    async def _emit_tool_result(
        self,
        tool_calls: List[Dict[str, Any]],
        results: List[Any],
        iteration: int,
    ) -> None:
        """Publish one ``thought_type=tool_result`` envelope per
        completed tool, AFTER dispatch. The ``segment_id`` matches
        the prior ``tool_call`` envelope so consumers can pair them.
        """
        ctx = self.ctx
        if not ctx.response_bus or not ctx.session_id or not ctx.interaction:
            return
        for tc, tr in zip(tool_calls, results):
            fn = tc.get("function", {})
            tool_name = fn.get("name") or "unknown"
            tool_call_id = tc.get("id") or ""
            # Suppress observability emit for tools whose effect is
            # already user-visible (response_publish, etc.).
            if _suppress_tool_observability(tool_name):
                continue
            # Idempotency: skip if we've already published this
            # tool_result envelope. Pairs with the matching guard
            # in ``_emit_tool_call``. See the doc-comment on
            # ``_emitted_envelopes``.
            dedupe_key = (tool_call_id, "tool_result")
            if dedupe_key in self._emitted_envelopes:
                continue
            self._emitted_envelopes.add(dedupe_key)
            raw_content = getattr(tr, "content", None)
            if raw_content is None and isinstance(tr, dict):
                raw_content = tr.get("content")
            # ToolExecutor JSON-stringifies non-string returns before
            # storing them on ``ToolResult.content`` (see
            # ``jvagent/tooling/tool_executor.py``). Reverse that for
            # streaming consumers so they receive the original dict
            # / list / scalar — much more useful than a string they
            # have to re-parse, and lets type-safe consumers (e.g.
            # the FE's StagedChange shape check) actually inspect
            # the value. Falls back to the raw string when parse
            # fails (error envelopes, plain text returns, etc.).
            tool_result: Any = raw_content
            if isinstance(raw_content, str) and raw_content:
                try:
                    tool_result = json.loads(raw_content)
                except (json.JSONDecodeError, ValueError):
                    tool_result = raw_content
            is_error = bool(
                getattr(tr, "is_error", False)
                or (
                    isinstance(tr, dict)
                    and isinstance(tr.get("content"), str)
                    and tr["content"].startswith("Error:")
                )
            )
            await ctx.response_bus.publish(
                session_id=ctx.session_id,
                content=("error" if is_error else "ok") + f": {tool_name}",
                channel=ctx.channel,
                # stream=False — discrete envelope, see _emit_tool_call.
                stream=False,
                interaction_id=ctx.interaction.id,
                interaction=ctx.interaction,
                user_id=ctx.user_id,
                streaming_complete=True,
                transient=True,
                category="thought",
                thought_type="tool_result",
                segment_id=tool_call_id or f"iter{iteration}-{tool_name}",
                metadata={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_result": tool_result,
                    "is_error": is_error,
                    "iteration": iteration,
                },
            )


# Late import to avoid circular dependency
from jvagent.action.cockpit.registry.assembler import assemble_cockpit_tools
