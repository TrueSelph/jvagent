"""``ReasoningHelm``: pure engine-loop helm orchestrated by Bridge.

ADR-0009 simplification: the router subsystem is gone. Each ``step()``
either initialises the engine session (first visit) or runs one
``engine.step()`` (revisit) and translates the result into a helm verb
Bridge dispatches:

- tool-call iterations → ``CONTINUE`` (engine called tools internally;
  needs another walker visit to feed observations back)
- final response → ``EMIT(via_persona=True, finalize=True)`` (Bridge
  routes through PersonaAction for stylisation)
- terminal with queued ``pending_ias`` → ``DELEGATE(...)`` chain (the
  engine's ``delegate_to_ia`` recovery hatch appended to the slot, or a
  prior visit did)
- nothing left → ``YIELD``

The engine receives the full registered tool surface (harness tools,
synchronous IAs, skill tools) and discovers what it needs across
iterations. There is no router pre-selection, no regime detection, no
capability filtering by class. Skills materialise as engine tools via
the registry's bundle-based discovery; IAs reach the engine via the
``delegate_to_ia`` tool (anchorless conversational recovery hatch).

Multi-iteration walker-revisit state lives on
``bridge_state.helm_states["ReasoningHelm"]`` (per-turn dict). The
``pending_ias`` slot is the dispatch queue for the post-engine DELEGATE
chain — the engine tool writes to it, ``_step_impl`` reads from it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR
from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import (
    CONTINUE,
    DELEGATE,
    EMIT,
    YIELD,
    HelmStepResult,
)
from jvagent.action.helm.reasoning.catalog.skill_catalog import SkillCatalog
from jvagent.action.helm.reasoning.catalog.skill_discovery import (
    list_always_active_skill_names,
)
from jvagent.action.helm.reasoning.config import EngineConfig
from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.action.helm.reasoning.engine import Engine
from jvagent.action.helm.reasoning.registry.shim import EngineVisitorShim
from jvagent.action.helm.reasoning.session import (
    EngineSession,
    clear_session,
    get_session,
)

if TYPE_CHECKING:
    from jvagent.action.bridge.state import BridgeState
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

ENGINE_DEFAULT_SKILL_MODEL: str = "claude-sonnet-4-20250514"


class ReasoningHelm(BaseHelm):
    """Deliberate-class reasoning helm: pure think-act-observe loop.

    Orchestrated by :class:`BridgeInteractAction`. Each ``step()`` call
    issues at most one model call (ADR-0002 invariant) by delegating to
    the duplicated :class:`Engine`. The helm dispatches its own
    tools internally and signals back to Bridge via ``CONTINUE`` /
    ``EMIT`` / ``DELEGATE`` / ``YIELD``.

    Tool surface (ADR-0009): harness tools + synchronous IA tools +
    skill tools (via registry bundle discovery) + ``delegate_to_ia``
    recovery hatch. No router pre-selection.
    """

    # ``weight`` is intentionally absent — helms are not InteractActions and
    # do not participate in the walker's weight ordering. Bridge (the
    # surrounding InteractAction) carries the weight=-200 attribute that
    # determines turn slot in the walker's queue.

    description: str = attribute(
        default=(
            "Reasoning helm: pure think-act-observe loop with full tool autonomy "
            "(harness + skill + IA-delegation surface). No router pre-selection."
        )
    )

    # Helm-protocol attributes (from BaseHelm).
    latency_class: str = attribute(
        default="deliberate",
        description="ReasoningHelm runs a heavy model loop; emit ack-on-shift "
        "when a peer SHIFTs into this helm.",
    )
    can_emit_directly: bool = attribute(default=True)

    # Per-turn orchestration state slot keys. Live under
    # ``bridge_state.helm_states[self.helm_name()]``; rebuilt per turn.
    # ClassVar annotations keep these as plain class-level strings.
    _STEP_OUTCOME_SLOT: ClassVar[str] = "step_outcome"
    _PENDING_FINAL_EMIT_SLOT: ClassVar[str] = "pending_final_emit"
    _PENDING_IAS_SLOT: ClassVar[str] = "pending_ias"

    def _get_helm_slot(self, visitor: "InteractWalker") -> Optional[Dict[str, Any]]:
        """Return this helm's per-turn state dict from BridgeState, or None.

        None when Bridge orchestration is bypassed (tests, or a future
        non-Bridge invocation path). Callers must defend against None.
        """
        bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if bridge_state is None:
            return None
        return bridge_state.helm_states.setdefault(self.helm_name(), {})

    def _get_step_outcome(self, visitor: "InteractWalker") -> Optional[str]:
        slot = self._get_helm_slot(visitor)
        if slot is None:
            return None
        return slot.get(self._STEP_OUTCOME_SLOT)

    def _set_step_outcome(
        self, visitor: "InteractWalker", value: Optional[str]
    ) -> None:
        slot = self._get_helm_slot(visitor)
        if slot is None:
            return
        if value is None:
            slot.pop(self._STEP_OUTCOME_SLOT, None)
        else:
            slot[self._STEP_OUTCOME_SLOT] = value

    def _get_pending_final_emit(
        self, visitor: "InteractWalker"
    ) -> Optional[Dict[str, Any]]:
        slot = self._get_helm_slot(visitor)
        if slot is None:
            return None
        val = slot.get(self._PENDING_FINAL_EMIT_SLOT)
        return val if isinstance(val, dict) else None

    def _set_pending_final_emit(
        self,
        visitor: "InteractWalker",
        value: Optional[Dict[str, Any]],
    ) -> None:
        slot = self._get_helm_slot(visitor)
        if slot is None:
            return
        if value is None:
            slot.pop(self._PENDING_FINAL_EMIT_SLOT, None)
        else:
            slot[self._PENDING_FINAL_EMIT_SLOT] = value

    def _ensure_interaction(self, visitor: "InteractWalker") -> bool:
        """Return True iff the visitor carries a valid interaction."""
        return getattr(visitor, "interaction", None) is not None

    # NOTE: legacy ``router_*`` attributes were removed in Wave 9b
    # (ADR-0009 follow-up). agent.yaml entries that still carry them
    # boot fine — the loader's unknown-context-key warning names the
    # agent + action + offending key, and the orphan value is dropped
    # silently from the action's context dict.

    model_action_type: str = attribute(default="AnthropicLanguageModelAction")
    model: str = attribute(default=ENGINE_DEFAULT_SKILL_MODEL)
    skills: Any = attribute(default=None)
    denied_skills: List[str] = attribute(default_factory=list)
    skills_source: str = attribute(default="both")
    max_iterations: int = attribute(default=25)
    max_duration_seconds: float = attribute(default=300.0)
    max_dynamic_activations: int = attribute(default=10)
    response_mode: str = attribute(default="publish")

    history_limit: int = attribute(default=3)
    max_statement_length: Optional[int] = attribute(default=None)
    enable_accumulation: bool = attribute(default=True)

    model_temperature: float = attribute(default=0.3)
    model_max_tokens: int = attribute(default=8192)

    reasoning_budget_tokens: int = attribute(default=0)
    reasoning_enabled: Optional[bool] = attribute(default=True)
    reasoning_effort: Optional[str] = attribute(default="medium")
    reasoning_extra: Optional[Dict[str, Any]] = attribute(default=None)

    stream_internal_progress: bool = attribute(default=True)

    max_concurrent_tools: int = attribute(default=5)
    tool_call_timeout: float = attribute(default=60.0)
    sanitize_tool_errors: bool = attribute(default=True)
    tool_servers: List[str] = attribute(default_factory=list)

    enable_skill_helper_tools: bool = attribute(default=True)
    enable_artifact_tools: bool = attribute(default=True)
    enable_capability_search: bool = attribute(default=True)
    tool_tier: str = attribute(default="standard")  # minimal | standard | full

    block_raw_tool_invocation: bool = attribute(default=True)
    preload_user_memory: bool = attribute(default=True)
    user_memory_max_chars: int = attribute(default=4096)
    auto_track_tasks: bool = attribute(default=True)
    skill_index_inline_max_skills: int = attribute(default=5)
    plan_first: bool = attribute(default=True)
    max_task_plan_steps: int = attribute(default=50)

    degenerate_response_max_chars: int = attribute(default=25)
    stuck_detection_window: int = attribute(default=4)
    stuck_intent_jaccard_threshold: float = attribute(default=0.65)
    stuck_primary_tool_repeat: int = attribute(default=4)
    stuck_min_iterations: int = attribute(default=4)

    # Overridable prompt templates (mirrors PersonaAction.system_prompt pattern).
    system_prompt: str = attribute(default="")
    task_planning_prompt: str = attribute(default="")
    security_prompt: str = attribute(default="")
    capability_search_prompt: str = attribute(default="")
    citation_instruction: str = attribute(default="")

    def _build_engine_config(self) -> EngineConfig:
        return EngineConfig(
            model=self.model,
            model_temperature=self.model_temperature,
            model_max_tokens=self.model_max_tokens,
            model_action_type=self.model_action_type,
            max_iterations=self.max_iterations,
            max_duration_seconds=self.max_duration_seconds,
            max_concurrent_tools=self.max_concurrent_tools,
            tool_call_timeout=self.tool_call_timeout,
            sanitize_tool_errors=self.sanitize_tool_errors,
            stuck_detection_window=self.stuck_detection_window,
            stuck_intent_jaccard_threshold=self.stuck_intent_jaccard_threshold,
            stuck_primary_tool_repeat=self.stuck_primary_tool_repeat,
            stuck_min_iterations=self.stuck_min_iterations,
            plan_first=self.plan_first,
            max_task_plan_steps=self.max_task_plan_steps,
            skills=self.skills,
            denied_skills=list(self.denied_skills or []),
            skills_source=self.skills_source,
            response_mode=self.response_mode,
            stream_internal_progress=bool(self.stream_internal_progress),
            block_raw_tool_invocation=bool(self.block_raw_tool_invocation),
            enable_skill_helper_tools=self.enable_skill_helper_tools,
            enable_artifact_tools=self.enable_artifact_tools,
            enable_capability_search=self.enable_capability_search,
            max_dynamic_activations=self.max_dynamic_activations,
            tool_tier=self.tool_tier,
            preload_user_memory=self.preload_user_memory,
            user_memory_max_chars=self.user_memory_max_chars,
            auto_track_tasks=self.auto_track_tasks,
            skill_index_inline_max_skills=self.skill_index_inline_max_skills,
            history_limit=self.history_limit,
            max_statement_length=self.max_statement_length,
            reasoning_budget_tokens=self.reasoning_budget_tokens,
            reasoning_enabled=self.reasoning_enabled,
            reasoning_effort=self.reasoning_effort,
            reasoning_extra=self.reasoning_extra,
            degenerate_response_max_chars=self.degenerate_response_max_chars,
            tool_servers=list(self.tool_servers or []),
            system_prompt=self.system_prompt or "",
            task_planning_prompt=self.task_planning_prompt or "",
            security_prompt=self.security_prompt or "",
            capability_search_prompt=self.capability_search_prompt or "",
            citation_instruction=self.citation_instruction or "",
        )

    @staticmethod
    def _strip_model_action_type(value: Any) -> Optional[str]:
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    def _language_model_action_type_for_purpose(self, purpose: str) -> Optional[str]:
        # Single model surface after ADR-0009 router elimination.
        # ``purpose`` retained for backward-compat with callers; any
        # value falls through to the skill model type.
        del purpose
        return self._strip_model_action_type(getattr(self, "model_action_type", None))

    async def get_model_action(
        self,
        required: bool = False,
        *,
        purpose: str = "skill",
    ) -> Optional[Any]:
        from jvagent.action.model.language.base import LanguageModelAction

        type_name = self._language_model_action_type_for_purpose(purpose)
        model_action: Optional[Any]
        if type_name:
            model_action = await self.get_action(type_name)
            if model_action and isinstance(model_action, LanguageModelAction):
                return model_action

        model_action = await self.get_action(LanguageModelAction)
        if model_action:
            return model_action

        if required:
            agent = await self.get_agent()
            agent_id = agent.id if agent else "unknown"
            raise RuntimeError(
                f"Model action for purpose '{purpose}' not found for agent '{agent_id}'"
            )
        return None

    async def _require_persona(self) -> Any:
        """Resolve and validate the persona action (duck-typed, no PersonaAction import)."""
        persona: Any = await self.get_action("PersonaAction")
        agent = await self.get_agent()
        aid = getattr(agent, "id", None) or "unknown"
        if persona is None or not getattr(persona, "enabled", True):
            raise RuntimeError(
                f"ReasoningHelm requires an enabled PersonaAction on agent '{aid}'."
            )
        desc = (getattr(persona, "persona_description", None) or "").strip()
        if not desc:
            raise RuntimeError(
                f"ReasoningHelm requires non-empty PersonaAction.persona_description on agent '{aid}'."
            )
        return persona

    # ------------------------------------------------------------------
    # Step entry point
    # ------------------------------------------------------------------

    async def _step_impl(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Bridge entry point: run one engine step + translate to verb.

        Verb routing:

        - ``CONTINUE`` — engine called tools and wants another visit.
        - ``DELEGATE(follow_up=...)`` — ``pending_ias`` slot has at
          least one IA queued (engine's ``delegate_to_ia`` tool, or a
          prior DELEGATE chain visit). Pops the head and dispatches.
        - ``EMIT(via_persona=True, finalize=True)`` — engine produced a
          final response; Bridge stylises via PersonaAction.
        - ``YIELD`` — terminal with nothing left to do.

        Called by :meth:`BaseHelm.step` (the wrapper handles the
        action-trace self-recording via
        ``interaction.record_action_execution``).
        """
        # Mid-chain dispatch: if pending_ias is populated, pop the next
        # one and return DELEGATE without re-running the engine.
        helm_slot = bridge_state.helm_states.setdefault(self.helm_name(), {})
        pending = list(helm_slot.get(self._PENDING_IAS_SLOT) or [])
        if pending:
            next_ia = pending[0]
            remaining = pending[1:]
            helm_slot[self._PENDING_IAS_SLOT] = remaining
            return DELEGATE(
                interact_action=next_ia,
                follow_up=bool(remaining),
            )

        # Fresh visit (or engine still in flight): run orchestration.
        # Reset outcome marker so stale state from a prior visit can't
        # leak into the next decision.
        self._set_step_outcome(visitor, None)

        try:
            await self._orchestrate(visitor)
        except Exception as exc:
            logger.warning(
                "ReasoningHelm: unhandled exception in _orchestrate: %s",
                exc,
                exc_info=True,
            )
            self._set_step_outcome(visitor, "yield")

        outcome = self._get_step_outcome(visitor)
        if outcome == "continue":
            return CONTINUE(reason="reasoning engine requested another visit")

        # ADR-0009 / Wave 9f: pending IA dispatch beats pending emit.
        # When the engine's ``delegate_to_ia`` tool fires alongside a
        # ``response_publish`` (model may call both in the same
        # iteration), the IA owns the user-facing response. Letting
        # EMIT win would publish the engine's impersonation text and
        # silently drop the IA dispatch — the IA's session would never
        # acquire its turn lock and subsequent turns would lose
        # auto-DELEGATE coverage. Cascade failure observed live against
        # the signup interview flow.
        new_pending = list(helm_slot.get(self._PENDING_IAS_SLOT) or [])
        if new_pending:
            # Clear any pending emit so the IA owns the response.
            self._set_pending_final_emit(visitor, None)
            next_ia = new_pending[0]
            remaining = new_pending[1:]
            helm_slot[self._PENDING_IAS_SLOT] = remaining
            return DELEGATE(
                interact_action=next_ia,
                follow_up=bool(remaining),
            )

        # No pending IA dispatch — hand the engine's final response to
        # Bridge as an EMIT(via_persona=True) so Bridge owns persona
        # stylisation.
        pending_emit = self._get_pending_final_emit(visitor)
        if pending_emit is not None:
            self._set_pending_final_emit(visitor, None)
            return EMIT(
                text=pending_emit.get("text", ""),
                finalize=True,
                via_persona=True,
                response_mode=self.response_mode,
                degenerate_max_chars=self.degenerate_response_max_chars,
                metadata={
                    "activated_skills": list(
                        pending_emit.get("activated_skills") or []
                    ),
                },
            )

        return YIELD()

    async def _orchestrate(self, visitor: "InteractWalker") -> None:
        """Engine-style orchestration body.

        On first visit: initialise engine session, run first step.
        On revisits: reuse engine, run next step.

        Side-effects only — sets ``step_outcome`` to signal to
        :meth:`_step_impl` whether Bridge should re-enqueue
        (``"continue"``) or finalise (``"yield"``).
        """
        if not self._ensure_interaction(visitor):
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            return

        if not hasattr(visitor, "_skill_state"):
            visitor._skill_state = {}
        visitor._skill_state.setdefault("action", self)

        session = get_session(visitor)

        # Stale-state guard: if the engine is from a different interaction,
        # reset the session so a fresh setup runs on the new user message.
        if session.engine is not None and session.interaction_id != interaction.id:
            logger.debug(
                "ReasoningHelm: stale engine from interaction %s, "
                "current interaction %s — clearing and re-setting up",
                session.interaction_id,
                interaction.id,
            )
            session.reset()

        if session.engine is None:
            # Fresh visit: set up engine + run first step.
            await self._setup_and_first_step(visitor)
        else:
            # Revisit: skip setup, reuse engine, run next step.
            await self._phase_continue(visitor)

    async def _setup_and_first_step(self, visitor: "InteractWalker") -> None:
        """First visit: build engine session, run engine.step() once.

        Replaces the router-driven setup-and-dispatch (ADR-0008's
        ``_phase_route_and_setup``). No router LM call, no regime
        detection, no IA pre-resolution.
        """
        try:
            persona = await self._require_persona()
            await self._start_engine(visitor, persona)
        except Exception as exc:
            logger.warning(
                "ReasoningHelm: error setting up engine session: %s",
                exc,
                exc_info=True,
            )
            await self._handle_error(visitor, exc)

    async def _phase_continue(self, visitor: "InteractWalker") -> None:
        """Revisit: reuse engine instance and run next step.

        Defensive against a cleared session — surface a fallback message
        rather than silently dropping the turn (AUDIT-interact-cockpit
        CRIT-02 mitigation preserved).
        """
        session = get_session(visitor)
        engine = session.engine
        if engine is None:
            logger.warning(
                "ReasoningHelm: revisit without engine; "
                "publishing fallback so the turn is not silently dropped"
            )
            interaction = visitor.interaction
            try:
                if interaction is not None and not getattr(
                    interaction, "response", None
                ):
                    fallback = (
                        "Sorry — I lost track of that step. "
                        "Could you rephrase or try again?"
                    )
                    interaction.response = fallback
                    try:
                        await interaction.save()
                    except Exception:
                        pass
                    if visitor.response_bus and visitor.session_id:
                        try:
                            await self.publish(
                                visitor=visitor,
                                content=fallback,
                                stream=False,
                                streaming_complete=True,
                            )
                        except Exception:
                            pass
                if interaction is not None and hasattr(interaction, "set_to_executed"):
                    try:
                        interaction.set_to_executed()
                        await interaction.save()
                    except Exception:
                        pass
            finally:
                session.debug_state = None
            self._set_step_outcome(visitor, "yield")
            return

        # AUDIT-interact HIGH-02: enforce a per-interaction step cap that
        # survives engine rebuilds (engine._iteration resets to 0 on rebuild).
        session.total_steps_this_interaction = (
            session.total_steps_this_interaction or 0
        ) + 1
        if session.total_steps_this_interaction > max(1, int(self.max_iterations) * 2):
            logger.warning(
                "ReasoningHelm: per-interaction step cap exceeded "
                "(%d steps; max_iterations=%d, ceiling=2x). Terminating turn.",
                session.total_steps_this_interaction,
                self.max_iterations,
            )
            await self._handle_error(
                visitor,
                RuntimeError(
                    "Per-interaction engine step cap exceeded — turn terminated"
                ),
            )
            return

        try:
            step_result = await engine.step()
            await self._handle_step_result(visitor, engine, step_result)
        except Exception as exc:
            logger.warning(
                "ReasoningHelm: error in phase_continue: %s",
                exc,
                exc_info=True,
            )
            await self._handle_error(visitor, exc)

    async def _start_engine(
        self,
        visitor: "InteractWalker",
        persona: Any,
    ) -> None:
        """Set up the engine context and run the first step.

        Tool surface is the full registered set (harness + IAs +
        skills). Preloaded skills come from always-active skills only —
        the engine discovers other skills through ``capability_search``
        and ``skill_activate``.
        """
        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            return

        cfg = self._build_engine_config()
        agent = getattr(visitor, "_agent", None)

        try:
            always_active = await list_always_active_skill_names(
                self, agent, conversation
            )
        except Exception:
            always_active = []
        preloaded = list(always_active)

        agent_name = getattr(persona, "persona_name", "Agent")
        agent_description = getattr(persona, "persona_description", "")

        model_action = await self.get_model_action(required=True)

        # Skill discovery for prompt construction. Persists the resolved
        # catalog and underlying discovered_skills dict on
        # visitor._skill_state so the engine, registry, and harness tools
        # (skill_*, capability_search) read the same source of truth.
        try:
            visitor_shim = EngineVisitorShim(
                agent,
                None,
                user_id=getattr(visitor, "user_id", None),
                conversation=conversation,
                interaction=interaction,
                session_id=visitor.session_id,
                response_bus=visitor.response_bus,
                channel=getattr(visitor, "channel", None),
            )
            catalog = await SkillCatalog.discover(
                visitor=visitor_shim,
                skills_selector=cfg.skills,
                skills_source=cfg.skills_source,
                denied_skills=cfg.denied_skills or None,
            )
            if catalog is not None:
                visitor._skill_state["skill_catalog"] = catalog
                visitor._skill_state["discovered_skills"] = dict(catalog.skills)
        except Exception as exc:
            logger.warning("ReasoningHelm: skill discovery for prompt failed: %s", exc)

        visitor._skill_state["interact_walker"] = visitor

        ctx = EngineContext(
            utterance=visitor.utterance or "",
            conversation=conversation,
            interaction=interaction,
            agent=agent,
            model_action=model_action,
            config=cfg,
            response_bus=visitor.response_bus,
            session_id=visitor.session_id or "",
            channel=getattr(visitor, "channel", "default"),
            stream=getattr(visitor, "stream", False),
            user_id=getattr(visitor, "user_id", None),
            persona=persona,
            action=self,
            visitor=visitor,
            preloaded_skills=preloaded,
            routed_skills=[],
            publish_callback=self._build_publish_callback(visitor),
        )

        engine = Engine(ctx)
        await engine.initialize()

        session = get_session(visitor)
        session.engine = engine
        session.interaction_id = interaction.id
        session.total_steps_this_interaction = (
            session.total_steps_this_interaction or 0
        ) + 1

        step_result = await engine.step()
        await self._handle_step_result(visitor, engine, step_result)

        # Reference unused identifiers to keep mypy quiet across edits.
        _ = (agent_name, agent_description, EngineSession)

    async def _handle_step_result(
        self,
        visitor: "InteractWalker",
        engine: Engine,
        step_result: Any,
    ) -> None:
        """Process a step result: revisit for tool calls, deliver for final response."""
        interaction = visitor.interaction
        if not interaction:
            return

        session = get_session(visitor)
        status = getattr(step_result, "status", "")

        if status == "tool_calls":
            # Model called tools — persist state. The helm requests
            # another Bridge visit via the CONTINUE verb instead of
            # mutating the walker queue directly.
            session.debug_state = engine.save_state()
            # Check if response_publish or delegate_to_ia set finalized.
            if session.finalized:
                session.reset()
                interaction.set_to_executed()
                self._set_step_outcome(visitor, "yield")
                return
            self._set_step_outcome(visitor, "continue")
            return

        # Terminal state: final_response, timeout, budget_exhausted, stuck.
        session.reset()
        interaction.set_to_executed()
        self._set_step_outcome(visitor, "yield")

        final_response = getattr(step_result, "final_response", "") or ""
        if final_response.strip():
            self._set_pending_final_emit(
                visitor,
                {
                    "text": final_response,
                    "activated_skills": list(
                        getattr(step_result, "activated_skills", []) or []
                    ),
                },
            )

    def _build_publish_callback(self, visitor: "InteractWalker"):
        """Build the callback that routes publish/thought events to the response bus."""

        async def _publish_cb(
            content: str,
            *,
            category: str,
            thought_type: Optional[str],
            segment_id: Optional[str],
            streaming_complete: bool,
            relay_to_adapters: bool,
        ) -> None:
            if category == "thought":
                await self.publish_thought(
                    visitor=visitor,
                    content=content,
                    thought_type=thought_type or "reasoning",
                    segment_id=segment_id,
                    streaming_complete=streaming_complete,
                    relay_to_adapters=relay_to_adapters,
                    allow_empty=not content,
                )
            elif content:
                await self.publish(
                    visitor=visitor,
                    content=content,
                    streaming_complete=streaming_complete,
                )

        return _publish_cb

    async def _handle_error(self, visitor: "InteractWalker", exc: Exception) -> None:
        """Handle errors during orchestration.

        Clears session state, publishes a fallback message on the response
        bus AND sets ``interaction.response`` (for non-bus channels), and
        signals Bridge to finalise.
        """
        clear_session(visitor)
        fallback_text = (
            "I encountered an error processing your request. Please try again."
        )
        interaction = visitor.interaction
        already_has_response = bool(
            interaction and getattr(interaction, "response", None)
        )
        if not already_has_response:
            try:
                await self.publish(
                    visitor=visitor,
                    content=fallback_text,
                    streaming_complete=True,
                )
            except Exception as pub_exc:
                logger.warning(
                    "reasoning_helm: failed to publish error fallback to bus: %s",
                    pub_exc,
                )
            if interaction is not None:
                interaction.response = fallback_text
                try:
                    await interaction.save()
                except Exception:
                    pass
        self._set_step_outcome(visitor, "yield")

    async def healthcheck(self) -> bool:
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        agent = await self.get_agent()
        if not agent:
            return True
        persona: Any = await self.get_action("PersonaAction")
        if not persona or not getattr(persona, "enabled", True):
            return False
        if not (getattr(persona, "persona_description", None) or "").strip():
            return False
        return True

    @classmethod
    async def refresh_skills(cls, visitor: "InteractWalker") -> List[str]:
        """Re-discover skills and merge newly installed bundles into the live session."""
        state = getattr(visitor, "_skill_state", None)
        if state is None:
            logger.warning("refresh_skills: no _skill_state on visitor")
            return []

        agent = getattr(visitor, "_agent", None)
        if agent is None:
            return []

        action = state.get("action")
        discovered_skills: Dict[str, Any] = state.get("discovered_skills") or {}
        skill_catalog = state.get("skill_catalog")

        await SkillCatalog.invalidate_cache(
            namespace=agent.namespace,
            agent_name=agent.name,
        )
        new_catalog = await SkillCatalog.discover(
            visitor=visitor,
            skills_selector=getattr(action, "skills", None) if action else None,
            skills_source=(
                getattr(action, "skills_source", "both") if action else "both"
            ),
            denied_skills=getattr(action, "denied_skills", None) if action else None,
        )
        new_skills = new_catalog.skills
        newly_found = [name for name in new_skills if name not in discovered_skills]

        if not newly_found and new_skills.keys() == discovered_skills.keys():
            return []

        discovered_skills.update(new_skills)
        state["discovered_skills"] = discovered_skills
        if skill_catalog is not None:
            skill_catalog.skills = discovered_skills

        logger.info(
            "refresh_skills: merged %d new skill(s): %s",
            len(newly_found),
            newly_found,
        )
        return newly_found

    @classmethod
    async def remove_skill(cls, visitor: "InteractWalker", skill_name: str) -> bool:
        """Hot-unload *skill_name* from the current engine session."""
        state = getattr(visitor, "_skill_state", None)
        if state is None:
            return False

        discovered_skills = state.get("discovered_skills") or {}
        skill_catalog = state.get("skill_catalog")

        if skill_name not in discovered_skills:
            return False

        discovered_skills.pop(skill_name, None)
        state["discovered_skills"] = discovered_skills
        if skill_catalog and isinstance(getattr(skill_catalog, "skills", None), dict):
            skill_catalog.skills.pop(skill_name, None)

        agent = getattr(visitor, "_agent", None)
        if agent is not None:
            await SkillCatalog.invalidate_cache(
                namespace=agent.namespace,
                agent_name=agent.name,
            )

        logger.info("remove_skill: removed skill '%s' from session", skill_name)
        return True
