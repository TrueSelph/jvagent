"""``ReasoningHelm``: Bridge helm running the engine-style engine loop.

Duplicated from ``jvagent/action/cockpit/cockpit_interact_action.py`` at
commit ``4bc6db6`` per the C-strategy hard constraint (BRIDGE-ROADMAP §C):
zero source-level coupling between Bridge and Cockpit.

Differences vs the standalone-Cockpit ancestor:

1. Subclass of :class:`BaseHelm` (not ``InteractAction``). Helms are
   orchestrated by :class:`BridgeInteractAction`, which owns the walker
   queue, shift budget, gear trace, and AC gating.
2. ``step(visitor, bridge_state)`` replaces ``execute(visitor)``. Returns a
   :class:`HelmStepResult` verb that Bridge dispatches:

   - tool-call iterations → :class:`CONTINUE` (helm dispatched its own
     tools internally via the duplicated engine)
   - routed ``interact_actions`` (IA-only or engine + IAs) → a chain of
     :class:`DELEGATE` verbs, ``follow_up=True`` for every entry except
     the tail (``follow_up=False`` on the last so Bridge runs persona-
     finalize and closes the turn). Each DELEGATE is one walker visit;
     Bridge runs the IA inline and re-enqueues this helm to dispatch
     the next.
   - terminal engine states with no queued IAs (final response /
     timeout / stuck / budget) → :class:`YIELD` (delivery already
     published by ``deliver_final_response`` via :class:`PersonaAction`)
   - SUPPRESS posture / interaction missing / persona broken → :class:`YIELD`
3. Walker-queue curation is delegated to Bridge entirely. The standalone
   Cockpit's ``curate_walk_path_for_cockpit`` call (which mutated the walker queue
   to schedule routed IAs after revisits) is removed — Bridge owns the
   queue via its own ``_curate_walker_queue``, and routed IAs are
   dispatched through the DELEGATE chain instead of being queued for
   walker iteration.
4. ``visitor.unrecord_action_execution()`` calls are dropped — the walker
   records only :class:`BridgeInteractAction` visits; helms are invisible
   to the walker's action trace.
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
from jvagent.action.helm.reasoning.delivery.delegation import (
    resolve_routed_interact_actions,
)
from jvagent.action.helm.reasoning.engine import Engine
from jvagent.action.helm.reasoning.registry.access import (
    filter_routed_interact_actions_by_access,
    filter_routed_skills_by_access,
)
from jvagent.action.helm.reasoning.registry.shim import EngineVisitorShim
from jvagent.action.helm.reasoning.routing.types import POSTURE_RESPOND, RoutingResult
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

# ReasoningHelm-owned per-run engine state lives on a single
# ``EngineSession`` object accessed via ``get_session``. Bridge-level state
# (current_helm, gear_trace, shift_count, etc.) is on
# ``visitor._bridge_state``; the two are independent.


def _routing_clarification_fallbacks_default() -> List[str]:
    from jvagent.action.helm.reasoning.routing.router import (
        ROUTING_CLARIFICATION_FALLBACK_MESSAGES,
    )

    return list(ROUTING_CLARIFICATION_FALLBACK_MESSAGES)


class ReasoningHelm(BaseHelm):
    """Deliberate-class reasoning helm: think-act-observe loop.

    Orchestrated by :class:`BridgeInteractAction`. Each ``step()`` call
    issues at most one model call (ADR-0002 invariant) by delegating to
    the duplicated :class:`Engine`. The helm dispatches its own
    tools internally and signals back to Bridge via ``CONTINUE`` /
    ``EMIT`` / ``YIELD``.

    Phase 1 (``EngineRouter``) and Phase 2 (engine loop) mirror
    the engine equivalent; the duplication source for each module is
    documented in ``jvagent/action/helm/reasoning/DUPLICATION_NOTICE.md``.
    """

    # ``weight`` is intentionally absent — helms are not InteractActions and
    # do not participate in the walker's weight ordering. Bridge (the
    # surrounding InteractAction) carries the weight=-200 attribute that
    # determines turn slot in the walker's queue.

    description: str = attribute(
        default=(
            "Reasoning helm: route posture/intent, grant the model full agency "
            "over harness services and action tools in a think-act-observe loop."
        )
    )

    # Helm-protocol attributes (from BaseHelm).
    latency_class: str = attribute(
        default="deliberate",
        description="ReasoningHelm runs a heavy model loop; emit ack-on-shift "
        "when a peer SHIFTs into this helm.",
    )
    can_emit_directly: bool = attribute(default=True)

    # Per-turn orchestration state slot keys (Wave-2 review item H3,
    # May 2026). Previously these lived as instance attributes on the
    # ReasoningHelm singleton (one Action instance shared by all
    # concurrent interactions on the agent). Two simultaneous turns
    # would cross-pollute — one turn's ``_step_outcome = "yield"`` would
    # be observed by another turn's ``_step_impl`` if the schedulers
    # interleaved. Now both fields live under
    # ``bridge_state.helm_states[self.helm_name()]`` which is per-turn
    # (BridgeState is rebuilt fresh each interaction).
    # ClassVar annotations keep these as plain class-level strings;
    # without them Pydantic treats a single-underscore-prefixed name
    # as a ``ModelPrivateAttr`` and assigns a descriptor instead of
    # the raw value, breaking ``slot[self._STEP_OUTCOME_SLOT]`` lookups.
    _STEP_OUTCOME_SLOT: ClassVar[str] = "step_outcome"
    _PENDING_FINAL_EMIT_SLOT: ClassVar[str] = "pending_final_emit"

    def _get_helm_slot(
        self, visitor: "InteractWalker"
    ) -> Optional[Dict[str, Any]]:
        """Return this helm's per-turn state dict from BridgeState, or None.

        None when Bridge orchestration is bypassed (tests, or a future
        non-Bridge invocation path). Callers must defend against None
        and treat absent state as a sensible default ("no pending emit
        yet", etc.).
        """
        bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if bridge_state is None:
            return None
        return bridge_state.helm_states.setdefault(self.helm_name(), {})

    def _get_step_outcome(self, visitor: "InteractWalker") -> Optional[str]:
        """Per-turn outcome marker for the most recent ``_orchestrate`` call.

        Read by :meth:`_step_impl` after orchestration returns to decide
        between ``CONTINUE`` and ``YIELD``. Set by the deep
        orchestration branches in ``_phase_route_and_setup`` /
        ``_phase_continue`` / ``_handle_step_result`` / ``_handle_error``.
        """
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
        """Per-turn buffer for the final EMIT(via_persona=True) payload.

        Set by ``_handle_step_result`` when the engine produces a
        non-empty final response; consumed by ``_step_impl`` on the
        next read and cleared. Bridge handles persona stylisation.
        """
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
        """Lift of ``InteractAction._ensure_interaction``.

        Helms inherit from :class:`BaseHelm` / :class:`Action`, not
        :class:`InteractAction`, so the helper is re-declared here. Identical
        semantics: return True iff the visitor carries a valid interaction.
        """
        return getattr(visitor, "interaction", None) is not None

    router_model: str = attribute(default="gpt-4o-mini")
    router_model_action_type: str = attribute(default="")

    model_action_type: str = attribute(default="AnthropicLanguageModelAction")
    model: str = attribute(default=ENGINE_DEFAULT_SKILL_MODEL)
    skills: Any = attribute(default=None)
    denied_skills: List[str] = attribute(default_factory=list)
    skills_source: str = attribute(default="both")
    max_iterations: int = attribute(default=25)
    max_duration_seconds: float = attribute(default=300.0)
    max_dynamic_activations: int = attribute(default=10)
    response_mode: str = attribute(default="publish")

    # NOTE — surfaces deliberately absent (vs the monolithic Cockpit
    # ancestor at jvagent.action.cockpit.cockpit_interact_action):
    #
    # - ``enable_canned_response`` / ``canned_response_max_words`` /
    #   ``skip_canned_for_intents``: ReflexHelm owns the user-facing
    #   immediate response via ``transient_ack`` on SHIFT. Canned
    #   surface is permanently absent from ReasoningHelm.
    # - ``converse_enabled`` / ``conversational_fast_path`` /
    #   ``converse_persona_prompt`` / ``converse_context_limit``: the
    #   conversational fast-path (skip-engine, persona.respond) was
    #   removed in the Phase-2 distillation. Smalltalk that reaches
    #   ReasoningHelm (pathological — Reflex should EMIT it directly)
    #   runs through the engine like any other turn. ReasoningHelm's
    #   sole mission is now agentic looping + skill/IA routing.

    history_limit: int = attribute(default=3)
    max_statement_length: Optional[int] = attribute(default=None)
    enable_accumulation: bool = attribute(default=True)

    router_model_temperature: float = attribute(default=0.1)
    router_model_max_tokens: int = attribute(default=400)

    model_temperature: float = attribute(default=0.3)
    model_max_tokens: int = attribute(default=8192)

    reasoning_budget_tokens: int = attribute(default=0)
    reasoning_enabled: Optional[bool] = attribute(default=True)
    reasoning_effort: Optional[str] = attribute(default="medium")
    reasoning_extra: Optional[Dict[str, Any]] = attribute(default=None)

    # Single switch for internal-progress streaming
    # (model thoughts, reasoning chunks, tool progress badges).
    stream_internal_progress: bool = attribute(default=True)

    max_concurrent_tools: int = attribute(default=5)
    tool_call_timeout: float = attribute(default=60.0)
    sanitize_tool_errors: bool = attribute(default=True)
    tool_servers: List[str] = attribute(default_factory=list)

    enable_skill_helper_tools: bool = attribute(default=True)
    enable_artifact_tools: bool = attribute(default=True)
    enable_capability_search: bool = attribute(default=True)
    tool_tier: str = attribute(default="standard")  # minimal | standard | full

    # Phase 1 latency knob — opt into the in-process router cache.
    # Cache keys fold active-task fingerprints so fragments routed
    # mid-interview don't share keys with the same fragment after the
    # interview completes. TTL is governed by perf config
    # ``interact_router_cache_ttl`` (default 45s).
    #
    # The standalone-Cockpit ancestor also exposes
    # ``enable_router_preclassifier`` for short-circuiting the router LLM
    # on smalltalk; ReasoningHelm omits it because Reflex catches
    # smalltalk upstream (sub-200ms EMIT) and the preclassifier never
    # fires in Bridge composition.
    enable_interact_router_cache: bool = attribute(default=False)

    # Hygiene flags. Each one is independently tunable; there is no umbrella
    # toggle. ``block_raw_tool_invocation`` defends the engine prompt against
    # users naming tools by name (``"call capability_search ..."``,
    # ``"/skill X"``, ``"execute Y"``); when True it injects the
    # ``SECURITY_BLOCK`` into the engine system prompt instructing the
    # model to treat user text as content, not commands. Default ``True``
    # — turn off only on agents that intentionally want to expose tool
    # dispatch through natural language (rare).
    block_raw_tool_invocation: bool = attribute(default=True)
    router_use_capability_search: bool = attribute(default=False)
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
    # Defaults are empty strings — engine falls back to module-level constants
    # in engine.prompts when the override is blank.  Set in agent.yaml to
    # customise engine behaviour without forking the framework.
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
            router_model=self.router_model,
            router_model_action_type=self.router_model_action_type or "",
            router_model_temperature=self.router_model_temperature,
            router_model_max_tokens=self.router_model_max_tokens,
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
            router_use_capability_search=self.router_use_capability_search,
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
        skill = self._strip_model_action_type(getattr(self, "model_action_type", None))
        router = self._strip_model_action_type(
            getattr(self, "router_model_action_type", None)
        )
        if purpose == "skill":
            return skill
        if purpose == "router":
            return router or skill
        return skill

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

    # Key under ``bridge_state.helm_states[helm_name]`` where pending
    # ``DELEGATE`` targets are queued (BRIDGE-ROADMAP §C-6 follow-up:
    # IA-tail dispatch via DELEGATE chain). Each entry is an
    # ``InteractAction`` class name (string). The list is mutated only
    # from inside this helm — Bridge does not touch it.
    _PENDING_IAS_SLOT = "pending_ias"

    def _queue_pending_ias(
        self,
        visitor: "InteractWalker",
        routed_ias: List[Any],
    ) -> None:
        """Save routed IA class names to the helm slot for DELEGATE chain.

        Reads ``bridge_state`` via the centralised visitor attribute
        (``BRIDGE_STATE_VISITOR_ATTR``) so the side-channel name is not
        duplicated. Truncates the list to ``max_dynamic_activations`` so
        a misbehaving router can't queue an unbounded chain.

        No-op when Bridge state is missing or ``routed_ias`` is empty.
        """
        if not routed_ias:
            return
        bridge_state = getattr(visitor, BRIDGE_STATE_VISITOR_ATTR, None)
        if bridge_state is None:
            logger.warning(
                "ReasoningHelm: cannot queue pending IAs — visitor has no "
                "bridge_state (Bridge orchestration appears bypassed)."
            )
            return
        cap = max(1, int(self.max_dynamic_activations or 10))
        names = [ia.__class__.__name__ for ia in routed_ias[:cap]]
        if len(routed_ias) > cap:
            logger.info(
                "ReasoningHelm: routed_ias truncated to max_dynamic_activations=%d "
                "(was %d entries; dropped tail)",
                cap,
                len(routed_ias),
            )
        slot = bridge_state.helm_states.setdefault(self.helm_name(), {})
        # If a chain is already in flight (engine + IAs path on a revisit),
        # extend rather than replace — the routing call only fires once
        # per turn, so this is defensive against a future code path that
        # re-routes.
        existing = list(slot.get(self._PENDING_IAS_SLOT) or [])
        slot[self._PENDING_IAS_SLOT] = existing + names

    async def _step_impl(
        self,
        visitor: "InteractWalker",
        bridge_state: "BridgeState",
    ) -> HelmStepResult:
        """Bridge entry point: run one engine step + translate to verb.

        Three return paths:

        - ``CONTINUE`` — engine called tools and wants another visit.
        - ``DELEGATE(follow_up=...)`` — one or more routed
          ``InteractAction``s were queued during routing (or earlier
          this turn) and the helm is dispatching them sequentially.
          ``follow_up=True`` for every entry except the last; ``False``
          on the final entry so Bridge runs persona-finalize and
          closes the turn.
        - ``YIELD`` — terminal (persona already published by the engine,
          or there's nothing left to do).

        Called by :meth:`BaseHelm.step` (the wrapper handles the
        action-trace self-recording via
        ``interaction.record_action_execution``).
        """
        # Mid-chain dispatch: if a prior visit populated pending IAs
        # (routing returned them, or engine terminated with IAs queued),
        # pop the next one and return DELEGATE without re-running
        # orchestration. The chain runs to completion before any further
        # routing happens this turn.
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

        # Orchestration produced a final engine response. Hand it to
        # Bridge as an EMIT(via_persona=True) so Bridge owns persona
        # stylisation. ``deliver_final_response`` is no longer called
        # in-line — the Phase-2 distillation pushed the persona-delivery
        # contract up into ``BridgeInteractAction._handle_emit``.
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

        # Orchestration completed without a final response. It may have
        # populated pending_ias (IA-only branch, or engine + IAs branch
        # on terminal). If so, pop the first and start the DELEGATE
        # chain. Otherwise yield.
        new_pending = list(helm_slot.get(self._PENDING_IAS_SLOT) or [])
        if new_pending:
            next_ia = new_pending[0]
            remaining = new_pending[1:]
            helm_slot[self._PENDING_IAS_SLOT] = remaining
            return DELEGATE(
                interact_action=next_ia,
                follow_up=bool(remaining),
            )

        # Nothing left to do this turn (no engine output, no queued IAs).
        return YIELD()

    async def _orchestrate(self, visitor: "InteractWalker") -> None:
        """Engine-style orchestration body (renamed from ``execute``).

        On first visit: route, set up engine, run first step.
        On revisits: restore engine state, run next step.

        Side-effects only — sets ``self._step_outcome`` to signal to
        :meth:`step` whether Bridge should re-enqueue (``"continue"``) or
        finalise (``"yield"``).
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
        # reset the session so routing runs on the fresh user message.
        if session.engine is not None and session.interaction_id != interaction.id:
            logger.debug(
                "ReasoningHelm: stale engine from interaction %s, "
                "current interaction %s — clearing and re-routing",
                session.interaction_id,
                interaction.id,
            )
            session.reset()

        if session.engine is None:
            # Fresh visit: Phase 1 (routing) + Phase 2 setup
            await self._phase_route_and_setup(visitor)
        else:
            # Revisit: skip routing, reuse engine, run next step
            pass  # helms not recorded by walker (Bridge owns trace)  # Avoid duplicate recording on revisit
            await self._phase_continue(visitor)

    async def _phase_route_and_setup(self, visitor: InteractWalker) -> None:
        """First visit: route, gate, dispatch to engine and/or interact_actions.

        Dispatch matrix (after posture + conversational gating):

        - ``routing.actions`` only          → engine path (existing)
        - ``routing.interact_actions`` only → skip engine, hand off to those IAs
        - both                              → engine first, IAs prepended on terminal
        - neither                           → engine path (engine handles via
          harness tools / model decides)
        """
        interaction = visitor.interaction

        try:
            from jvagent.action.helm.reasoning.routing.router import EngineRouter

            # EngineRouter only emits RESPOND-class results in Bridge
            # composition (Reflex gates SUPPRESS/DEFER upstream and owns
            # the transient_ack lead-in). The conversational fast-path
            # that the monolithic Cockpit ancestor used to skip the
            # engine on smalltalk has also been removed — if a
            # CONVERSATIONAL classification reaches here at all, the
            # engine handles it. Reflex misroutes are observable and
            # rare; the simplicity of one path is worth more than
            # avoiding a single engine call on the rare miss.
            router = EngineRouter(self)
            _posture, routing = await router.route(visitor)

            if routing is None:
                routing = RoutingResult(posture=POSTURE_RESPOND)

            persona = await self._require_persona()
            agent = await self.get_agent()
            user_id = getattr(visitor, "user_id", None)
            channel = getattr(visitor, "channel", "default") or "default"

            # Apply per-user access control before resolving / dispatching.
            # Skills routed by the LLM are filtered against
            # ``skill:{name}`` rules; interact_actions are filtered against
            # their class names (existing access_control convention).
            routing.actions = await filter_routed_skills_by_access(
                agent, routing, user_id=user_id, channel=channel
            )

            # Resolve and AC-filter routed interact_actions. In Bridge
            # composition we do NOT curate the walker queue here — Bridge
            # owns it via its own ``_curate_walker_queue`` (which already
            # restricts the queue to ``{Bridge} ∪ always_execute IAs``
            # on first visit). Routed IAs are dispatched one at a time
            # via the DELEGATE chain in :meth:`step` instead.
            routed_ias = await resolve_routed_interact_actions(agent, routing)
            routed_ias = await filter_routed_interact_actions_by_access(
                agent, routed_ias, user_id=user_id, channel=channel
            )

            has_skills = bool(routing.actions)
            has_ias = bool(routed_ias)

            # interact_actions only → skip engine, hand off to IAs.
            # The curate above already placed the routed IAs in the walker queue
            # in weight order; the walker will visit them after the engine returns.
            # We then append the engine to the END of the walk path so it runs once
            # more after the IAs to invoke PersonaAction with the accumulated
            # directives — that's the user-facing response.
            session = get_session(visitor)

            if has_ias and not has_skills:
                # IA-only: queue the IAs for sequenced DELEGATE dispatch
                # via :meth:`step`. No engine call this turn — Bridge
                # runs each IA through its ``_handle_delegate`` and
                # finalises via persona after the LAST DELEGATE
                # (``follow_up=False`` on the tail). Cap chain length at
                # ``max_dynamic_activations`` so a misbehaving router
                # can't queue an unbounded number of activations.
                self._queue_pending_ias(visitor, routed_ias)
                logger.info(
                    "ReasoningHelm: routing returned IA-only=%s; queued "
                    "for DELEGATE chain (length=%d)",
                    [a.__class__.__name__ for a in routed_ias],
                    len(routed_ias),
                )
                self._set_step_outcome(visitor, "yield")
                return

            # both → run engine first; queue the IAs for post-engine
            # DELEGATE chain. The engine path proceeds normally; on
            # terminal state, :meth:`step` finds the queued IAs and
            # starts the chain. Bridge finalises via persona after the
            # last DELEGATE in the chain.
            if has_ias and has_skills:
                self._queue_pending_ias(visitor, routed_ias)
                logger.info(
                    "ReasoningHelm: routing returned engine + IAs=%s; "
                    "queued for post-engine DELEGATE chain (length=%d)",
                    [a.__class__.__name__ for a in routed_ias],
                    len(routed_ias),
                )

            # skills only OR neither → engine path.
            await self._start_engine(visitor, routing, persona)

        except Exception as exc:
            logger.warning(
                "ReasoningHelm: error in phase_route_and_setup: %s",
                exc,
                exc_info=True,
            )
            await self._handle_error(visitor, exc)

    async def _phase_continue(self, visitor: InteractWalker) -> None:
        """Revisit: reuse engine instance and run next step.

        If the engine was cleared between prepend and revisit (stale-state
        guard, error handler ``clear_session``, or a tool calling
        ``response_publish(finalize=True)`` resetting the session), the
        previous implementation silently returned without publishing or
        marking the interaction executed. The walker then moved on with no
        user-facing output for the turn. AUDIT-interact-cockpit CRIT-02.

        Now: surface a generic fallback response, mark the interaction
        executed, and clear remaining session state so the walker continues
        cleanly without a dangling run.
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
        visitor: InteractWalker,
        routing: RoutingResult,
        persona: Any,
    ) -> None:
        """Set up the engine and run the first step."""
        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            return

        cfg = self._build_engine_config()
        agent = getattr(visitor, "_agent", None)
        agent_name = getattr(persona, "persona_name", "Agent")
        agent_description = getattr(persona, "persona_description", "")

        preloaded = list(routing.actions)
        try:
            always_active = await list_always_active_skill_names(
                self, agent, conversation
            )
        except Exception:
            always_active = []
        for name in always_active:
            if name not in preloaded:
                preloaded.append(name)

        # NOTE: the standalone-Cockpit ancestor filters a synthetic
        # "converse" skill out of the engine's preloaded list here.
        # ReasoningHelm has no fast-path that would inject "converse"
        # as a route (the CONVERSATIONAL→converse promotion in
        # EngineRouter is also stripped), so the filter is unnecessary.

        if routing.actions:
            skills_csv = ", ".join(routing.actions)
            intent = routing.intent_type or "UNCLEAR"
            interp_line = (
                f" Interpretation: {routing.interpretation}"
                if routing.interpretation
                else ""
            )
            guidance = (
                "\n\n# Routing decision (advisory — verify before committing)\n"
                f"Intent: {intent}.{interp_line} "
                f"Router pre-selected skill(s): **{skills_csv}**. "
                "The SOP for these skills is inlined under '# Router-selected skill(s)' below, "
                "alongside a quick index of the other catalog skills.\n"
                "- If the recommended SOP fits the actual request, begin work with "
                "its tools and workflow immediately. Do NOT call `skill_read` for "
                "the recommended skill, and do NOT spend turns on memory_set, "
                "task_create_plan, or other harness scaffolding before invoking "
                "its primary tools.\n"
                "- If the recommendation is the wrong fit (e.g. user actually "
                "asked something the listed SOP does not cover), treat the "
                "router as fallible: pick a better skill from the peer index, "
                "call `skill_read` on it, and use ITS tools. Do NOT answer from "
                "world knowledge while a catalog skill could plausibly satisfy "
                "the request."
            )
            agent_description += guidance

        model_action = await self.get_model_action(required=True)

        # Skill discovery for prompt construction.
        # Persists the resolved catalog and underlying discovered_skills dict
        # on visitor._skill_state so the engine, registry, and harness tools
        # (skill_*, capability_search) can all read the same source of truth.
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
            routed_skills=list(routing.actions or []),
            publish_callback=self._build_publish_callback(visitor),
        )

        engine = Engine(ctx)
        await engine.initialize()

        # Persist engine instance and interaction ID for revisit detection.
        session = get_session(visitor)
        session.engine = engine
        session.interaction_id = interaction.id
        # AUDIT-interact HIGH-02: count the first step against the
        # per-interaction ceiling. Subsequent steps fire in _phase_continue.
        session.total_steps_this_interaction = (
            session.total_steps_this_interaction or 0
        ) + 1

        step_result = await engine.step()
        await self._handle_step_result(visitor, engine, step_result)

    async def _handle_step_result(
        self,
        visitor: InteractWalker,
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
            # Model called tools — persist state. The helm requests another
            # Bridge visit via the CONTINUE verb instead of mutating the
            # walker queue directly (Bridge owns visitor.prepend).
            session.debug_state = engine.save_state()
            # Check if response_publish set the finalized flag
            if session.finalized:
                # Tool already delivered the response — conclude
                session.reset()
                interaction.set_to_executed()
                self._set_step_outcome(visitor, "yield")
                return
            self._set_step_outcome(visitor, "continue")
            return

        # Terminal state: final_response, timeout, budget_exhausted, stuck.
        # IA tail dispatch (when routing returned interact_actions) is
        # handled by :meth:`step` reading ``helm_states[helm_name]["pending_ias"]``
        # and emitting a DELEGATE chain. We don't need to inspect
        # ``session.pending_interact_actions`` here — that field is a
        # engine-duplication artifact in :class:`EngineSession` that
        # Bridge code never populates.
        session.reset()
        interaction.set_to_executed()
        self._set_step_outcome(visitor, "yield")

        final_response = getattr(step_result, "final_response", "") or ""

        if final_response.strip():
            # Stash the final emit for :meth:`step` to convert into an
            # EMIT(via_persona=True) verb. Bridge's ``_handle_emit``
            # owns persona stylisation now (Phase-2 distillation pushed
            # ``deliver_final_response`` up into Bridge so ReasoningHelm
            # stays focused on agentic-loop + skill/IA routing). The
            # ``activated_skills`` list is forwarded via EMIT.metadata
            # so Bridge can resolve per-skill response_mode / verbatim
            # overrides against the skill catalog (still on
            # ``visitor._skill_state``).
            self._set_pending_final_emit(
                visitor,
                {
                    "text": final_response,
                    "activated_skills": list(
                        getattr(step_result, "activated_skills", []) or []
                    ),
                },
            )

    # ``_finalize_via_persona`` (the standalone Cockpit's IA-only mode finalizer) is
    # not duplicated here — Bridge handles persona-finalize itself after
    # the last DELEGATE in the chain via
    # ``BridgeInteractAction._finalize_via_persona_if_directives``.
    # Removed at the C-6 follow-up (IA-tail dispatch via DELEGATE chain).

    def _build_publish_callback(self, visitor: InteractWalker):
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

        Wave-1 review item H5 (May 2026) — the previous implementation
        only wrote to ``interaction.response``. Bus-subscribing channels
        (Slack adapter, WhatsApp adapter, the streaming web client) saw
        silence on engine errors because they consume the response-bus
        stream rather than re-reading the interaction record. Now we
        publish first (with ``streaming_complete=True`` so the bus
        flushes immediately) and write ``interaction.response`` as a
        fallback for channels that read the persisted record.
        """
        clear_session(visitor)
        fallback_text = (
            "I encountered an error processing your request. Please try again."
        )
        interaction = visitor.interaction
        # Skip publish if a real response was already streamed mid-loop —
        # the user already has something on screen; an error addendum
        # would be more confusing than helpful.
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
                # Don't let publish errors mask the original failure —
                # log and continue to the interaction-record fallback.
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
        # helms not recorded by walker (Bridge owns trace)
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
    async def refresh_skills(cls, visitor: InteractWalker) -> List[str]:
        """Re-discover skills and merge any newly installed bundles into the live session.

        The engine assembles its tool registry fresh on every step from
        ``visitor._skill_state["discovered_skills"]``, so updating that dict
        is sufficient — no per-call registration is needed.
        """
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
    async def remove_skill(cls, visitor: InteractWalker, skill_name: str) -> bool:
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
