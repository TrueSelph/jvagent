"""Think-act-observe loop for OrchestratorInteractAction."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from jvagent.action.orchestrator import continuation
from jvagent.action.orchestrator.constants import (
    _NON_SUBSTANTIVE_TOOLS,
    is_untrusted_directive_source,
)
from jvagent.action.orchestrator.loop_helpers import text_candidate as _text_candidate
from jvagent.action.orchestrator.prompts import (
    render_capabilities_section,
    render_skills_section,
)
from jvagent.action.parameters import (
    orchestration_parameters,
    render_parameters,
    reply_core_parameters,
)

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


def _orch():
    """Late-bound orchestrator module so tests can monkeypatch re-exported helpers."""
    from jvagent.action.orchestrator import orchestrator_interact_action

    return orchestrator_interact_action


class OrchestratorLoopMixin:
    async def _process_model_decision(
        self,
        visitor: "InteractWalker",
        decision: Optional[Dict[str, Any]],
        tools: Dict[str, Any],
        skill_names: Set[str],
        observations: List[Dict[str, Any]],
        pending_chain: Optional[str],
        chain_deflections: int,
        plan_deflections: int,
        nd_streak: int,
        active_skill_doc: Any,
    ) -> tuple[Optional[str], Optional[str], Dict[str, Any], int, int, int]:
        """Process a model decision and return action routing.

        Handles garbled decisions, normalization, final action deflections,
        and extracts action/tool_name/args for dispatch.

        Args:
            visitor: Current InteractWalker
            decision: Raw model decision (may be None if garbled)
            tools: Available tools dict
            skill_names: Set of skill names (for locked-skill guard)
            observations: Observation list to append feedback
            pending_chain: Pending chained tool name
            chain_deflections: Count of chain deflections this turn
            plan_deflections: Count of plan deflections this turn
            nd_streak: Consecutive garbled decision count
            active_skill_doc: Active skill doc (for locked-skill guard)

        Returns:
            Tuple of (action, tool_name, args_dict, chain_deflections, plan_deflections, nd_streak).
            action is None when the decision should be retried.
        """
        if decision is None:
            # A truncated/garbled decision (common when a verbose thinking
            # model overruns the token cap). One transient miss → nudge
            # and retry with the tool surface intact, so a productive turn
            # isn't aborted mid-task. Only a persistent streak falls
            # through to the partial-compose (work-done-but-can't-emit).
            nd_streak += 1
            if nd_streak >= 3:
                # Persistent failure - caller should end turn
                return (None, None, {}, chain_deflections, plan_deflections, nd_streak)
            observations.append(
                {
                    "tool": "(parse)",
                    "args": {},
                    "observation": (
                        "(Your previous response was not a single valid "
                        "JSON object. Reply with exactly ONE JSON object "
                        "for your next step — a tool call or a final "
                        "answer. Keep it short.)"
                    ),
                }
            )
            return (None, None, {}, chain_deflections, plan_deflections, nd_streak)
        nd_streak = 0
        action, tool_name, args = self._normalize(decision, tools, skill_names)
        if (
            action == "tool"
            and tool_name in skill_names
            and tool_name not in tools
            and active_skill_doc is not None
            and tool_name == getattr(active_skill_doc, "name", None)
        ):
            observations.append(
                {
                    "tool": tool_name,
                    "args": args,
                    "observation": (
                        f"({tool_name} is the active locked skill, not a "
                        "callable tool. Follow the ACTIVE SKILL procedure "
                        "and use its listed tools, or reply/respond to the "
                        "user. Do not invoke the skill name as a tool.)"
                    ),
                }
            )
            return (None, None, {}, chain_deflections, plan_deflections, nd_streak)
        # Progress/reasoning line for the UI's REASONING disclosure. Fires
        # on both gears so single-step (light) turns still show their
        # reasoning, not just multi-step heavy ones. Skip when substantive
        # tool thoughts will surface the same tick in TOOL CALLS.
        if self.stream_internal_progress:
            await self._emit_thought(
                visitor,
                self._progress_line(action, tool_name, args, decision),
            )
        if action == "final":
            if pending_chain and chain_deflections < 2:
                # A tool result told the model to call ``pending_chain``
                # next. Don't let it finalize (or claim completion) until
                # that step has run.
                chain_deflections += 1
                observations.append(
                    {
                        "tool": "(guard)",
                        "args": {},
                        "observation": (
                            f"(The task is not finished — call "
                            f"{pending_chain} now to continue. Do NOT give a "
                            "final answer or claim the process is "
                            "complete until it has run.)"
                        ),
                    }
                )
                return (None, None, {}, chain_deflections, plan_deflections, nd_streak)
            if plan_deflections < int(self.plan_completion_max_deflections):
                open_steps = self._open_plan_step(visitor)
                if open_steps:
                    # An active multi-step plan still has open steps —
                    # don't finalize mid-task. Nudge the model to run the
                    # next step (or close the plan if it's really done).
                    plan_deflections += 1
                    observations.append(self._plan_drain_nudge(open_steps))
                    return (None, None, {}, chain_deflections, plan_deflections, nd_streak)
        return (action, tool_name, args, chain_deflections, plan_deflections, nd_streak)

    async def _handle_locked_flow(
        self,
        visitor: "InteractWalker",
        flow_owner: str,
        tools: Dict[str, Any],
        activated: List[str],
        loop_t0: float,
        tool_timings: List[Dict[str, Any]],
    ) -> bool:
        """Execute hard-locked flow when lock_active_flow is enabled.

        When a control-task points to an IA that furnished a tool, restrict the
        callable surface to that one tool and dispatch it. The loop can only
        continue the flow, never route elsewhere.

        Args:
            visitor: Current InteractWalker
            flow_owner: Name of the flow owner tool
            tools: Available tools dict
            activated: List of activated skill names
            loop_t0: Loop start time
            tool_timings: List to append timing info

        Returns:
            True if the flow was handled and caller should return, False otherwise.
        """
        if not (self.lock_active_flow and flow_owner and flow_owner in tools):
            return False

        tool_t0 = time.perf_counter()
        try:
            if self.tool_call_timeout and self.tool_call_timeout > 0:
                locked_result = (
                    await asyncio.wait_for(
                        tools[flow_owner].run({}),
                        timeout=self.tool_call_timeout,
                    )
                ) or ""
            else:
                locked_result = (await tools[flow_owner].run({})) or ""
        except asyncio.TimeoutError:
            locked_result = (
                f"(tool error: locked flow {flow_owner} timed out after "
                f"{self.tool_call_timeout}s)"
            )
        tool_timings.append(
            {
                "name": flow_owner,
                "duration_ms": int((time.perf_counter() - tool_t0) * 1000),
            }
        )
        interaction = getattr(visitor, "interaction", None)
        # The locked IA "emits" either by setting a response OR by queuing a
        # directive (the directive-based publishing pattern — `_egress`
        # renders it after the loop). Checking only `interaction.response`
        # missed the directive path, so the orchestrator mistook a publishing
        # IA for a silent one and echoed the IA-as-tool status sentinel
        # ("(ran X)") as a spurious reply/directive (ADR-0013 follow-up).
        emitted = self._ia_emitted(interaction)
        ended = "locked"
        if not emitted:
            # The IA ran but produced nothing user-facing. NEVER echo its
            # internal status sentinel ("(ran X)" / "(no visitor available)"
            # / "(flow error: …)") — those are loop-internal. Surface a clean
            # message instead.
            #
            # EVERY non-emitting locked turn — access-denied, a thrown
            # error, or a silently-non-emitting IA — must count toward the
            # escape streak. Otherwise a flow that denies access (AC revoked
            # mid-flow) or runs without ever emitting/completing traps the
            # user behind the turn-lock forever: the same dead-end reply on
            # every subsequent turn, with no way to route elsewhere. After
            # LOCKED_FLOW_ERROR_LIMIT consecutive dead-ends the owning
            # control-task is abandoned so the next turn runs the loop.
            # AUDIT-orchestrator HIGH.
            res = locked_result.strip()
            if "access denied" in res.lower():
                ended = "locked_denied"
                reply = (
                    "You don't currently have access to continue this. Let "
                    "me know if there's something else I can help with."
                )
            elif res.startswith("(flow error") or res.startswith("(tool error"):
                ended = "locked_error"
                reply = self.clarify_text
            else:
                ended = "locked_silent"
                reply = self.clarify_text
            if await _orch().note_locked_flow_error(visitor, flow_owner):
                ended = f"{ended}_escape"
            await self._emit_reply(visitor, reply)
        else:
            # A working flow resets the failure streak.
            await _orch().clear_locked_flow_error(visitor, flow_owner)
        await self._record_orchestrator_activation(
            visitor,
            continuation_mode="locked",
            flow_owner=flow_owner,
            tools_invoked=[flow_owner],
            tick_count=0,
            ended_via=ended,
            activated=activated,
            loop_duration_ms=int((time.perf_counter() - loop_t0) * 1000),
            tool_timings=tool_timings,
        )
        await self._finalize_plan(visitor)
        return True

    async def _run_loop(self, visitor: "InteractWalker") -> None:
        loop_t0 = time.perf_counter()
        tool_timings: List[Dict[str, Any]] = []
        activated: List[str] = []
        visible: Set[str] = set()
        skill_docs: List[Any] = []
        utterance = getattr(visitor, "utterance", "") or ""

        # Accumulate every action's scoped params onto the interaction so they
        # flow through the subsystem of record (observable + deduped) and each
        # injection site renders its scope.
        interaction = getattr(visitor, "interaction", None)
        await self._accumulate_parameters(interaction)

        # Resolve the active flow first so the surface gates its tool into the
        # prompt only when relevant (active flow, or anchor-relevant utterance).
        flow_tool_names = await self._routable_flow_tool_names()
        flow_owner = _orch().active_flow_owner(visitor, flow_tool_names=flow_tool_names)
        surface_meta: Dict[str, Any] = {}
        tools = await self._assemble_tools(
            visitor, activated, visible, flow_owner, utterance, skill_docs, surface_meta
        )
        lean_surface = bool(surface_meta.get("lean"))
        skill_names = {getattr(d, "name", "") for d in skill_docs}
        blocked_skill_notes = surface_meta.get("blocked_skill_notes") or []
        skills_section = render_skills_section(skill_docs, blocked_skill_notes)
        # State the current channel explicitly. Skill docs are already filtered
        # to this channel (ADR-0032), so everything listed IS available here —
        # without this line the model has no ground truth for where it is and
        # can hallucinate a channel deny ("please message us on WhatsApp") to a
        # user who is already there, parroting deny copy from its knowledge.
        _channel = str(getattr(visitor, "channel", "") or "").strip()
        if _channel:
            skills_section = (
                f"CURRENT CHANNEL: {_channel}. Every skill listed below is "
                "available on this channel — never tell the user to switch "
                "channels to use one of them.\n\n" + skills_section
            )
        # Advertised abilities: each enabled action's get_capabilities() merged
        # with the skill descriptions. Sourced from the actions/skills directly
        # (not the lean-surfaced tool list), so it stays complete even when most
        # callable tools are hidden behind find_tool — the model then never
        # under-claims an ability ("I can't sign you up…" while holding the
        # signup flow). Stable for the turn.
        capabilities_section = render_capabilities_section(
            await self._collect_capabilities(skill_docs)
        )
        # Orchestration-scoped rules from the accumulated pool, rendered into the
        # system prompt — they govern how the executive reasons. Response-scoped
        # rules belong to the reply compose, not here. Falls back to this action's
        # own orchestration core when the interaction has no pool yet.
        # The orchestration rules govern how the executive reasons; the core
        # response params are applied here too as safeguards, because the
        # executive can author a user-facing reply directly (the fast ``reply``
        # path applies no compose-time shaping). The reply compose renders the
        # response set as well — whichever path produces user text is hardened.
        _pool = getattr(interaction, "parameters", None) or self.parameters
        parameters_section = render_parameters(
            orchestration_parameters(_pool) + reply_core_parameters()
        )
        self._turn_prompt_cache = {
            "identity": await self._render_identity(),
            "capabilities": capabilities_section,
            "parameters": parameters_section,
            "skills_section": skills_section,
        }

        # Hard turn-lock (lock_active_flow): when a control-task points to an IA
        # that furnished a tool, restrict the callable surface to that one tool
        # and dispatch it — the loop can only continue the flow, never route
        # elsewhere. The IA's tool is visitor-bound, AC-gated, and terminal
        # (so it owns the turn's output). The IA receives all input including
        # off-topic; interruption/cancel is the IA's own concern.
        if await self._handle_locked_flow(
            visitor, flow_owner, tools, activated, loop_t0, tool_timings
        ):
            return

        if flow_owner and flow_owner not in tools:
            # Locked-in skill tasks use the skill name as owner_action — they
            # are not routable IA tools, so exempt them from the orphan sweep.
            # Skip the sweep entirely when action enumeration failed — an empty
            # or partial tool map must not cancel a healthy in-progress flow.
            if getattr(self, "_actions_enum_failed", False):
                logger.warning(
                    "orchestrator: skipping orphan flow sweep — action "
                    "enumeration failed (owner=%s)",
                    flow_owner,
                )
            else:
                _locked_skill_names: Set[str] = {
                    d.name
                    for d in skill_docs
                    if getattr(d, "task_lock", False) and getattr(d, "name", None)
                }
                await _orch().cancel_orphan_flow_tasks(
                    visitor,
                    routable_tool_names=set(tools.keys()),
                    locked_skill_names=_locked_skill_names,
                )
                flow_owner = _orch().active_flow_owner(
                    visitor, flow_tool_names=flow_tool_names
                )

        flow_note = _orch().active_flow_note(flow_owner) if flow_owner else ""

        # Resumable-plan note (ADR-0019): a multi-step plan recorded on a prior
        # turn that still has pending steps is re-surfaced so the model resumes
        # it instead of re-planning. Resolved once at turn start (a plan the
        # model creates *this* turn doesn't need a resume note). Soft, like the
        # flow note — never a hard lock.
        plan_note = (
            _orch().plan_resume_note(
                _orch().active_plan(visitor, owner=self.get_class_name())
            )
            if self.planning
            else ""
        )

        agent = await self._safe_agent()
        loop_actions = await self._enabled_actions(agent) if agent else [self]

        observations: List[Dict[str, Any]] = []
        # Pre-loop upload ingestion (ADR-0021 S4): persist EVERY uploaded file in
        # visitor.data to per-user storage as ONE consolidated source="upload"
        # artifact each — images enriched in place with a per-image
        # interpretation (file + understanding in a single artifact). Returns the
        # image interpretation(s) to seed the loop. Best-effort; never blocks.
        try:
            vision_seed = await self._ingest_uploads(visitor)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("orchestrator: upload ingestion failed: %s", exc)
            vision_seed = ""
        # Fallback to the standalone vision reflex only when ingestion produced
        # no image interpretation (e.g. ingest_uploads disabled) — preserves
        # vision for that config without double-interpreting images.
        if not vision_seed:
            vision_seed = await self._vision_reflex(visitor)
        if vision_seed:
            observations.append(
                {
                    "tool": "interpret_images",
                    "args": {},
                    "observation": f"Interpretation of attached image(s): {vision_seed}",
                }
            )
        else:
            # No new image, but the user may be referring back to one shown
            # earlier — deterministically recall its stored interpretation so a
            # weak model doesn't claim it can't remember (ADR-0021 S3).
            recall_seed = await self._artifact_recall_seed(visitor)
            if recall_seed:
                observations.append(
                    {
                        "tool": "get_artifact",
                        "args": {},
                        "observation": (
                            "Recalled interpretation of image(s) the user shared "
                            f"earlier in this conversation:\n{recall_seed}"
                        ),
                    }
                )

        await self._seed_proactive_dispatch(visitor, skill_docs, tools, observations)

        active_skill_doc = await self._find_active_task_lock_skill_doc(
            visitor, skill_docs, loop_actions
        )

        if active_skill_doc is None and self._should_auto_start_skills(
            visitor,
            skill_docs,
            active_skill_doc=active_skill_doc,
            flow_owner=flow_owner,
        ):
            first_locked = await self._seed_auto_start_skills(
                visitor, skill_docs, tools, observations
            )
            if first_locked is not None:
                active_skill_doc = first_locked
            elif activated:
                for doc in skill_docs:
                    if getattr(doc, "task_lock", False) and doc.name in activated:
                        active_skill_doc = doc
                        break

        if active_skill_doc is not None:
            prep_obs_before = len(observations)
            tools, visible, skills_section = await self._apply_active_task_lock_skill(
                active_skill_doc,
                loop_actions,
                visitor,
                utterance,
                tools,
                visible,
                activated,
                observations,
                skill_docs=skill_docs,
            )
            await self._emit_server_prep_tool_thoughts(
                visitor, observations, since_index=prep_obs_before
            )

        from jvagent.action.orchestrator.skill_tasks import (
            prune_task_lock_tools_for_actions,
        )

        await prune_task_lock_tools_for_actions(loop_actions, visitor, tools, visible)

        # Companion capabilities the active lock permits. Skill names gate
        # use_skill (a side-skill like FAQ is allowed; switching to an unrelated
        # skill is not). Tool names mark a companion detour so the parent task can
        # be re-grounded the moment the side request is handled.
        locked_companion_skill_names: Set[str] = set()
        locked_companion_tools: Set[str] = set()
        if active_skill_doc is not None:
            from jvagent.action.orchestrator.skill_tasks import (
                _companion_surface,
                resolve_lock_companions,
            )

            _companion_skills, _companion_globs = resolve_lock_companions(
                active_skill_doc, skill_docs
            )
            locked_companion_skill_names = {
                d.name for d in _companion_skills if getattr(d, "name", None)
            }
            _allowed, _ = _companion_surface(_companion_skills, _companion_globs, tools)
            # Tool names only — use_skill is handled by the skill-name branch.
            locked_companion_tools = {t for t in _allowed if t != "use_skill"}

        budget = max(
            1,
            int(
                self._channel_cfg(visitor, "activation_budget", self.activation_budget)
            ),
        )
        history = await self._history(visitor)
        ticks = 0
        ended_via = "budget"
        last_sig: Optional[tuple] = None
        last_obs: str = ""
        repeats = 0
        # Directive contract: a tool result carries the authoritative next step.
        # ``pending_chain`` holds a tool the model MUST call before it can finalize
        # (so it can't fabricate "you're all set" without running it); a terminal
        # "Tell the user or ask the user:" directive with no chain is the turn's reply and is
        # delivered directly in the loop body (so the model can't re-decide and
        # re-run the same tool). Both are enforced below — generically, no tool
        # is named in code.
        pending_chain: Optional[str] = None
        chain_deflections = 0
        # Plan-completion guard (thin, plan-gated): when an active multi-step plan
        # still has open steps, a bare "I'll do X next" narration (coerced to a
        # reply) or a premature ``final`` is deflected once so the loop keeps
        # going instead of ending the turn mid-task. Bounded so a deliberate
        # reply/finish is never blocked for long.
        plan_deflections = 0
        # Named-tool steering guard (block_raw_tool_invocation): tools the user
        # named literally this turn, deflected once each so the model re-plans
        # from intent rather than obeying the named tool.
        user_named_tools = (
            self._user_named_tools(utterance, set(tools.keys()))
            if self.block_raw_tool_invocation
            else frozenset()
        )
        deflected_named: Set[str] = set()
        # ADR-0034 L5 two-strike soft-abandon: evaluate the strike once per turn
        # at the companion gate, then reuse the decision on any repeat gate hit
        # within the same turn.
        soft_abandon_evaluated = False
        soft_abandon_streak = 0
        soft_abandon_title = ""
        nd_streak = 0  # consecutive unparseable model decisions
        # Model gearing (ADR-0016): light until the turn proves multi-step.
        substantive_tool_calls = 0
        ticks_light = 0
        ticks_heavy = 0
        started = time.time()
        max_duration_seconds = float(
            self._channel_cfg(
                visitor, "max_duration_seconds", self.max_duration_seconds
            )
            or 0.0
        )
        deadline = started + max_duration_seconds if max_duration_seconds > 0 else 0.0

        # The transient ack only applies once the heavy model is engaged (it is
        # the slow gear) — scheduled on the first heavy tick below, not up front.
        ack_task: Optional["asyncio.Task"] = None
        ack_started = False

        # Standing store drain (ADR-0026 §3/§2.4): the orchestrator watches the work
        # graph every turn, independent of any skill turn-lock. Dispatch non-skill
        # runnable tasks via their registered runners first; a task that blocks on
        # external input owns the turn's egress. Inert until a consumer registers a
        # runner — SKILL tasks fall through to the skill path/loop below.
        drain_directive = await self._drain_runnable_tasks(visitor, observations)
        if drain_directive:
            await self._send_reply(visitor, drain_directive, compose=True)
            ended_via = "drain_reply"
            return

        try:
            while budget > 0:
                if deadline and time.time() > deadline:
                    ended_via = "duration"
                    break
                budget -= 1
                ticks += 1
                # Gear selection (sticky): heavy once the turn is multi-step —
                # enough substantive tool calls, or a skill (multi-step SOP) is
                # active. Single-model agents always run heavy.
                gear = self._select_gear(
                    substantive_tool_calls,
                    bool(activated) or active_skill_doc is not None,
                )
                if gear == "light":
                    ticks_light += 1
                else:
                    ticks_heavy += 1
                # Arm the transient ack only once the turn proves COMPLEX — a
                # skill is active, or it has made multiple substantive tool calls.
                # Simple single-tool / reply-only turns never surface a "working
                # on it" line (and so it can't trail after a fast reply).
                if not ack_started and (
                    bool(activated)
                    or substantive_tool_calls >= int(self.escalate_after_tool_calls)
                ):
                    ack_started = True
                    ack_task = self._schedule_first_emit_ack(visitor)
                visible_tools = [tools[n] for n in visible if n in tools]
                decision = await self._run_model(
                    visitor,
                    utterance,
                    history,
                    visible_tools,
                    observations,
                    flow_note,
                    skills_section,
                    gear=gear,
                    lean=lean_surface,
                    plan_note=plan_note,
                    capabilities_section=capabilities_section,
                    parameters_section=parameters_section,
                )
                (
                    action,
                    tool_name,
                    args,
                    chain_deflections,
                    plan_deflections,
                    nd_streak,
                ) = await self._process_model_decision(
                    visitor,
                    decision,
                    tools,
                    skill_names,
                    observations,
                    pending_chain,
                    chain_deflections,
                    plan_deflections,
                    nd_streak,
                    active_skill_doc,
                )
                if action is None:
                    if nd_streak >= 3:
                        ended_via = "no_decision"
                        break
                    continue
                if action == "final":
                    answer = _text_candidate(decision)
                    if answer:
                        await self._maybe_emit_final(visitor, answer)
                    ended_via = "final"
                    return
                if action == "tool":
                    # Steering guard: the user named this exact tool — deflect it
                    # once so tool selection stays the agent's call, driven by the
                    # goal rather than the named tool.
                    if (
                        tool_name in user_named_tools
                        and tool_name not in deflected_named
                    ):
                        deflected_named.add(tool_name)
                        observations.append(
                            {
                                "tool": tool_name,
                                "args": args,
                                "observation": (
                                    f"(You may not call {tool_name} just because "
                                    "the user named it. Tool selection is your "
                                    "responsibility — work out the user's "
                                    "underlying goal and choose the right "
                                    "tool(s) yourself, or answer directly.)"
                                ),
                            }
                        )
                        continue
                    if (
                        pending_chain
                        and tool_name in ("reply", "respond")
                        and chain_deflections < 2
                    ):
                        # A chained step is pending — don't let the model reply
                        # (e.g. announce completion) before it runs.
                        chain_deflections += 1
                        observations.append(
                            {
                                "tool": "(guard)",
                                "args": {},
                                "observation": (
                                    f"(The task is not finished — call "
                                    f"{pending_chain} now, not reply/respond. Do NOT "
                                    "tell the user the process is complete until it has run.)"
                                ),
                            }
                        )
                        continue
                    if tool_name in ("reply", "respond") and plan_deflections < int(
                        self.plan_completion_max_deflections
                    ):
                        # Plan-drain: the orchestrator must not COMPLETE the turn
                        # (reply/respond is terminal egress) while its active plan
                        # still has unfinished steps — whether the reply is bare
                        # narration coerced to a reply ("Proceeding to drafting
                        # now") or a deliberate reply. Deflect and drive the model
                        # to do the next step or explicitly close the plan. After
                        # the cap the reply passes, so a genuine mid-plan question
                        # to the user is never blocked forever.
                        open_steps = self._open_plan_step(visitor)
                        if open_steps:
                            plan_deflections += 1
                            observations.append(self._plan_drain_nudge(open_steps))
                            continue
                    # Companion gate: while a skill holds the turn-lock, use_skill
                    # may only (re)activate the locked skill itself or a declared
                    # companion. Switching to an unrelated skill would abandon the
                    # active task — block it and steer back.
                    if (
                        active_skill_doc is not None
                        and tool_name == "use_skill"
                        and (args or {}).get("name")
                        and (args or {}).get("name") != active_skill_doc.name
                        and (args or {}).get("name") not in locked_companion_skill_names
                    ):
                        requested = (args or {}).get("name")
                        # ADR-0034 L5: a non-companion use_skill under a task-lock
                        # interview is a soft-abandon strike (the model chose to
                        # switch rather than answer the pending field). Count it
                        # once per turn and escalate deterministically: streak 1
                        # bounces, streak 2 also asks the one-turn switch question,
                        # and a streak past the ask == the user's "yes" — apply the
                        # skill's on_abandon and unlock so this same utterance
                        # re-routes on the now-free surface.
                        if not soft_abandon_evaluated:
                            soft_abandon_evaluated = True
                            collected = continuation.soft_abandon_collected_count(
                                getattr(visitor, "conversation", None)
                            )
                            soft_abandon_streak = (
                                await continuation.note_soft_abandon_strike(
                                    visitor,
                                    active_skill_doc.name,
                                    collected_count=collected,
                                )
                            )
                            soft_abandon_title = await continuation.soft_abandon_title(
                                agent, active_skill_doc
                            )
                            if (
                                soft_abandon_streak
                                > continuation.SOFT_ABANDON_ASK_STRIKE
                                and await continuation.apply_soft_abandon(
                                    visitor, agent, active_skill_doc.name
                                )
                            ):
                                active_skill_doc = None
                                locked_companion_skill_names = set()
                                locked_companion_tools = set()
                                continue
                        if soft_abandon_streak == continuation.SOFT_ABANDON_ASK_STRIKE:
                            observations.append(
                                {
                                    "tool": tool_name,
                                    "args": args,
                                    "observation": (
                                        f"({requested} cannot be started while "
                                        f"{active_skill_doc.name} is in progress. "
                                        'Before switching, ask the user: "Want me '
                                        f"to set aside the {soft_abandon_title} for "
                                        'now and help with that instead?" and wait '
                                        "for their answer.)"
                                    ),
                                }
                            )
                            continue
                        allowed = (
                            ", ".join(sorted(locked_companion_skill_names)) or "none"
                        )
                        observations.append(
                            {
                                "tool": tool_name,
                                "args": args,
                                "observation": (
                                    f"({requested} cannot be started "
                                    f"while {active_skill_doc.name} is in progress. "
                                    f"Permitted companions: {allowed}. Finish or "
                                    "cancel the active task first, then switch.)"
                                ),
                            }
                        )
                        continue
                    # Repeat guard (pre-dispatch): a model that re-issues the
                    # SAME call (tool + args) makes no progress, and re-running
                    # a side-effecting tool (queue a task, POST to an API)
                    # would duplicate its effects — so the duplicate is never
                    # dispatched. One re-dispatch is allowed when the prior
                    # attempt errored/timed out (transient failures deserve a
                    # retry); a third identical call ends the turn.
                    sig = (tool_name, str(args))
                    repeats = repeats + 1 if sig == last_sig else 0
                    last_sig = sig
                    if repeats >= 2:
                        ended_via = "repeat_guard"
                        return
                    if repeats == 1:
                        prior_errored = (
                            last_obs.startswith("(tool error:")
                            or " timed out after " in last_obs
                        )
                        if not prior_errored:
                            observations.append(
                                {
                                    "tool": "(guard)",
                                    "args": {},
                                    "observation": (
                                        f"(You have already called {tool_name} "
                                        "with this exact input; its result is "
                                        "above. Do NOT repeat the call — use a "
                                        "different tool, change the arguments, "
                                        'or finish with action "final".)'
                                    ),
                                }
                            )
                            continue
                    tool = tools.get(tool_name)
                    # Whether this iteration's ``obs`` is server-generated framing
                    # (always trusted for the directive contract) rather than a
                    # raw tool result. Set True wherever the loop constructs obs
                    # itself (e.g. the prerequisite detour below).
                    obs_server_generated = False
                    if tool is None:
                        # Genuinely unknown name (often a hallucinated tool) —
                        # this is where find_tool earns its keep: point the model
                        # at discovery instead of letting it guess again.
                        obs = (
                            f"(no such tool: {tool_name}. Call "
                            "find_tool(query) to find the right tool by "
                            "capability — e.g. find_tool('write file'), "
                            "find_tool('add to knowledge base') — then call the "
                            "exact name it returns.)"
                        )
                    else:
                        if self.block_raw_tool_invocation and tool_name not in visible:
                            # The model named a REAL tool that lean surfacing had
                            # hidden. Naming it IS effective intent (not a
                            # hallucination), so promote it and run it — an
                            # implicit load_tool — rather than dead-ending on a
                            # find_tool demand the model just repeats until the
                            # repeat-guard kills the turn. Dispatch already
                            # resolves the full surface; hiding a tool from the
                            # prompt never made it uncallable. (The user-named-tool
                            # steer guard above still blocks tools the *user*
                            # dictated.)
                            visible.add(tool_name)
                        # Structured tool thought for the UI's TOOL CALLS panel:
                        # tool_call before, tool_result after (shared segment_id
                        # so they fold into one element). Substantive tools only.
                        tool_seg = (
                            f"toolcall-{uuid.uuid4().hex[:10]}"
                            if tool_name not in _NON_SUBSTANTIVE_TOOLS
                            else None
                        )
                        if tool_seg:
                            await self._emit_tool_thought(
                                visitor, "tool_call", tool_name, tool_seg, args=args
                            )
                        # Voice-friendly ack: arm the transient ack before the
                        # FIRST substantive tool runs, so a slow tool (Flow
                        # send, web search) is covered by a spoken/visible
                        # "One moment…" instead of dead air. Still gated by
                        # first_emit_timeout_ms — fast tools surface nothing.
                        if (
                            not ack_started
                            and self.ack_on_first_tool_call
                            and tool_name not in _NON_SUBSTANTIVE_TOOLS
                        ):
                            ack_started = True
                            ack_task = self._schedule_first_emit_ack(visitor)
                        tool_call_timeout = float(
                            self._channel_cfg(
                                visitor, "tool_call_timeout", self.tool_call_timeout
                            )
                            or 0.0
                        )
                        tool_t0 = time.perf_counter()
                        try:
                            if tool_call_timeout > 0:
                                obs = await asyncio.wait_for(
                                    tool.run(args), timeout=tool_call_timeout
                                )
                            else:
                                obs = await tool.run(args)
                        except asyncio.TimeoutError:
                            obs = (
                                f"(tool {tool_name} timed out after "
                                f"{tool_call_timeout}s)"
                            )
                        except Exception as exc:
                            logger.warning(
                                "orchestrator: tool %r raised: %s", tool_name, exc
                            )
                            obs = f"(tool error: {exc})"
                        tool_timings.append(
                            {
                                "name": tool_name,
                                "duration_ms": int(
                                    (time.perf_counter() - tool_t0) * 1000
                                ),
                            }
                        )
                        # After (fires on success, timeout, or error — obs is
                        # always a string by here).
                        if tool_seg:
                            await self._emit_tool_thought(
                                visitor, "tool_result", tool_name, tool_seg, obs=obs
                            )
                    observations.append(
                        {"tool": tool_name, "args": args, "observation": obs}
                    )
                    last_obs = obs if isinstance(obs, str) else str(obs)
                    if tool_name == "use_skill":
                        skill_name = ((args or {}).get("name") or "").strip()
                        prep_obs_before = len(observations)
                        locked_doc, tools, visible, new_section, detour_directive = (
                            await self._apply_task_lock_after_use_skill(
                                skill_name=skill_name,
                                activation_obs=obs if isinstance(obs, str) else "",
                                skill_docs=skill_docs,
                                loop_actions=loop_actions,
                                visitor=visitor,
                                utterance=utterance,
                                tools=tools,
                                visible=visible,
                                activated=activated,
                                observations=observations,
                            )
                        )
                        if locked_doc is not None:
                            active_skill_doc = locked_doc
                            if new_section:
                                skills_section = new_section
                            await self._emit_server_prep_tool_thoughts(
                                visitor, observations, since_index=prep_obs_before
                            )
                            # A prerequisite was pushed: deliver its first question
                            # as the turn's terminal reply so the model cannot
                            # fabricate the answer and skip the gate. The directive
                            # contract below reads a JSON tool-result, so frame it as
                            # one (no next_tool ⇒ it is treated as the terminal reply).
                            if detour_directive:
                                obs = json.dumps(
                                    {"response_directive": detour_directive}
                                )
                                obs_server_generated = True
                        elif (
                            skill_name
                            and isinstance(obs, str)
                            and obs.startswith("Activated skill")
                        ):
                            # Non-task-lock: PROCEDURE lives in skills_section so
                            # Steps taken this turn stays TOOL-only.
                            from jvagent.action.orchestrator.skill_tasks import (
                                activated_skill_section_text,
                            )

                            doc = next(
                                (
                                    d
                                    for d in skill_docs
                                    if getattr(d, "name", None) == skill_name
                                ),
                                None,
                            )
                            if doc is not None:
                                skills_section = activated_skill_section_text(doc)
                    # Companion detour: a companion capability (tool or skill) was
                    # used while a parent skill holds the turn-lock. Re-ground the
                    # parent in place so the model returns to it as soon as the side
                    # request is handled — same turn, not next.
                    if active_skill_doc is not None and (
                        tool_name in locked_companion_tools
                        or (
                            tool_name == "use_skill"
                            and ((args or {}).get("name") or "").strip()
                            in locked_companion_skill_names
                        )
                    ):
                        rg_before = len(observations)
                        await self._reground_parent_lock(
                            active_skill_doc, loop_actions, visitor, observations
                        )
                        if len(observations) > rg_before:
                            await self._emit_server_prep_tool_thoughts(
                                visitor, observations, since_index=rg_before
                            )
                    # Directive contract (see loop-state init): a tool result may
                    # carry the authoritative next step. A pending ``next_tool`` is a
                    # chain the model MUST take before it can finalize; a bare
                    # "Tell the user or ask the user:" directive with no chain is the turn's reply,
                    # delivered directly so the model cannot re-decide (e.g. re-call
                    # the same tool). Generic — no tool is named in code.
                    if isinstance(obs, str):
                        # Trust boundary (AUDIT-orchestrator HIGH): only honor the
                        # directive contract from server-generated framing or a
                        # first-party tool. A raw MCP/third-party result is external
                        # content — parsing next_tool/response_directive from it
                        # would let a compromised server hijack the turn's reply or
                        # force tool-chaining.
                        if obs_server_generated or not is_untrusted_directive_source(
                            tool_name
                        ):
                            nt, rd = self._result_next(obs)
                        else:
                            nt, rd = None, ""
                        # Completion detection is safe to read regardless of the
                        # directive trust boundary: it consults only the completion
                        # flags (not the hijackable next_tool/response_directive), and
                        # the resume it triggers self-guards on real task state — a
                        # spoofed completion cannot choose which task resumes. Some
                        # first-party field-store tools are otherwise treated as
                        # untrusted here, which would hide a prerequisite's silent
                        # completion (one that carries no reply directive of its own).
                        obs_is_completion = self._result_is_completion(obs)
                        says_reply = rd.strip().lower().startswith("tell the user")
                        if nt:
                            # The result chains to another tool the model MUST call.
                            pending_chain = nt
                            chain_deflections = 0
                        elif says_reply or obs_is_completion:
                            # Drain (ADR-0026): when a task-lock skill completes —
                            # whether it emits a terminal reply ("tell the user…") or a
                            # silent completion (a prerequisite finishing with no
                            # user-facing reply of its own) — re-resolve the task lock.
                            # If a parent task is now the top
                            # runnable, resume it in THIS turn instead of leaving the
                            # resume to a model tick that may narrate past a first field
                            # the activation would auto-resolve. _maybe_resume self-guards:
                            # it no-ops unless obs marks a completion and a parent waits.
                            resumed = await self._maybe_resume_after_completion(
                                obs,
                                active_skill_doc,
                                skill_docs,
                                loop_actions,
                                visitor,
                                utterance,
                                tools,
                                visible,
                                activated,
                                observations,
                            )
                            if resumed is not None:
                                (
                                    active_skill_doc,
                                    tools,
                                    visible,
                                    skills_section,
                                    resume_terminal,
                                ) = resumed
                                if resume_terminal:
                                    # The resumed skill voices its own next question:
                                    # deliver it and end the turn (server-driven
                                    # resume — the model cannot fabricate the answer).
                                    await self._send_reply(
                                        visitor, resume_terminal, compose=True
                                    )
                                    ended_via = "resume_reply"
                                    return
                                # The parent's surface (and its server-side activation)
                                # is now applied; continue so the model finalizes on it.
                                continue
                            if says_reply:
                                # Terminal reply directive with no chain — deliver it
                                # and end so the model cannot re-decide (e.g. re-run a
                                # tool it already ran). Compose (not literal relay): the
                                # directive may carry model-facing guidance that must be
                                # rendered into the agent's voice, not leaked verbatim.
                                await self._send_reply(visitor, rd, compose=True)
                                ended_via = "directive_reply"
                                return
                        elif pending_chain and tool_name == pending_chain:
                            # The pending chain just ran and produced no further
                            # chain — it's satisfied; let the model finalize.
                            pending_chain = None
                            chain_deflections = 0
                    # Gearing: count substantive (non-meta, non-egress) tool calls
                    # toward escalation to the heavy model.
                    if tool is not None and tool_name not in _NON_SUBSTANTIVE_TOOLS:
                        substantive_tool_calls += 1
                    # End the turn once the user has been addressed: a persona
                    # reply (by name) or a terminal IA-tool that owns its own
                    # output. This also stops a model that keeps choosing the
                    # same tool from looping until the budget is exhausted.
                    if tool_name in ("reply", "respond") or (
                        tool is not None and tool.terminal
                    ):
                        ended_via = (
                            tool_name
                            if tool_name in ("reply", "respond")
                            else "ia_tool"
                        )
                        return
                    continue
                # Unknown action — stop rather than loop.
                ended_via = "unknown"
                return

            # Invariant 7 (ADR-0026): the loop ended, but the orchestrator must not
            # finalize idle while runnable work remains. Drain non-skill runnable
            # tasks now (some may have become runnable mid-turn — e.g. a completion
            # unblocked one); if one blocks on input it owns the egress. Inert until a
            # runner is registered, so skill-only turns are unaffected.
            interaction = getattr(visitor, "interaction", None)
            emitted = bool(getattr(interaction, "response", "") if interaction else "")
            if not emitted:
                drain_directive = await self._drain_runnable_tasks(
                    visitor, observations
                )
                if drain_directive:
                    await self._send_reply(visitor, drain_directive, compose=True)
                    ended_via = f"{ended_via}_drained"
                    return
                emitted = bool(
                    getattr(interaction, "response", "") if interaction else ""
                )

            # Budget/time ran out mid-task. Rather than dropping to the generic
            # clarify fallback (which discards the work and misreports the
            # cause), force ONE compose so the user gets the agent's best answer
            # from what it gathered. Only when there's actual work to summarize.
            if (
                not emitted
                and ended_via in ("budget", "duration", "no_decision")
                and observations
            ):
                decision = await self._run_model(
                    visitor,
                    utterance,
                    history,
                    [],
                    observations,
                    skills_section=skills_section,
                    finalize=True,
                    gear="light",  # wrap-up is single-dimensional
                    capabilities_section=capabilities_section,
                    parameters_section=parameters_section,
                )
                answer = _text_candidate(decision) if decision else ""
                if answer:
                    await self._maybe_emit_final(visitor, answer)
                    ended_via = f"{ended_via}_finalized"
        finally:
            if ack_task is not None and not ack_task.done():
                ack_task.cancel()
            # Plan lifecycle (ADR-0019): close a fully-done plan, leave a plan
            # with pending steps ACTIVE so the next turn resumes it. Runs on
            # every loop exit; no-op when planning is off.
            await self._finalize_plan(visitor)
            rec_continuation_mode = (
                "locked"
                if active_skill_doc
                else ("model_mediated" if flow_owner else "none")
            )
            rec_flow_owner = active_skill_doc.name if active_skill_doc else flow_owner
            await self._record_orchestrator_activation(
                visitor,
                continuation_mode=rec_continuation_mode,
                flow_owner=rec_flow_owner,
                tools_invoked=[o.get("tool") for o in observations],
                tick_count=ticks,
                ended_via=ended_via,
                activated=activated,
                ticks_light=ticks_light,
                ticks_heavy=ticks_heavy,
                loop_duration_ms=int((time.perf_counter() - loop_t0) * 1000),
                tool_timings=tool_timings,
            )
