"""CockpitInteractAction: model-cockpit InteractAction with walker-revisit pattern.

Plugs into the InteractWalker pipeline. Grants the language model full agency
over harness services (memory, response, task, conversation, skills) and
action tools via a think-act-observe loop.

Instead of an internal iteration loop, each walker visit executes ONE model call.
When the model returns tool calls, the action persists engine state on
``visitor._skill_state`` and re-adds itself to the walk path via
``visitor.prepend([self])``. When the model produces text (no tool calls),
the response is delivered and the cockpit run concludes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.cockpit.access import (
    filter_routed_interact_actions_by_access,
    filter_routed_skills_by_access,
)
from jvagent.action.cockpit.config import CockpitConfig
from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.action.cockpit.delegation import (
    collect_always_execute_interact_actions,
    curate_walk_path_for_cockpit,
    prepend_routed_interact_actions,
    resolve_routed_interact_actions,
)
from jvagent.action.cockpit.delivery import (
    deliver_conversational,
    deliver_final_response,
)
from jvagent.action.cockpit.engine import CockpitEngine
from jvagent.action.cockpit.gates import should_use_conversational_gate
from jvagent.action.cockpit.routing_types import POSTURE_RESPOND, RoutingResult
from jvagent.action.cockpit.shim import CockpitVisitorShim
from jvagent.action.cockpit.skill_catalog import SkillCatalog
from jvagent.action.cockpit.skill_discovery import list_always_active_skill_names
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.cockpit.router import CockpitRouter
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

COCKPIT_DEFAULT_SKILL_MODEL: str = "claude-sonnet-4-20250514"

# Keys in visitor._skill_state for cockpit iteration persistence
_COCKPIT_STATE_KEY = "cockpit_state"
_COCKPIT_ENGINE_KEY = "cockpit_engine"
_COCKPIT_INTERACTION_ID_KEY = "cockpit_interaction_id"
# Pending routed InteractActions queued by Phase 1 to run AFTER the engine
# reaches a terminal step. Set in "both" mode (skills + interact_actions).
_COCKPIT_PENDING_IAS_KEY = "cockpit_pending_interact_actions"
# Marker that cockpit has been appended to the END of the walker queue to
# perform a persona delivery after upstream InteractActions finish.
# Set in "interact_actions only" mode so directives accumulated by IAs
# are handed to PersonaAction.respond() for the final user-facing response.
_COCKPIT_FINALIZE_PENDING_KEY = "cockpit_ia_finalize_pending"


def _routing_clarification_fallbacks_default() -> List[str]:
    from jvagent.action.cockpit.router import ROUTING_CLARIFICATION_FALLBACK_MESSAGES

    return list(ROUTING_CLARIFICATION_FALLBACK_MESSAGES)


class CockpitInteractAction(InteractAction):
    """Model-cockpit InteractAction — full agency over harness + action tools.

    Phase 1 routes posture + skills via a lightweight LLM call.
    Phase 2 runs ``CockpitEngine`` which gives the main model access to every
    harness service and action tool.

    Each walker visit executes one model call. When the model returns tool calls,
    the action persists state and re-adds itself to the walk path. When the model
    produces a text response, it is delivered to the user.
    """

    weight: int = attribute(default=-200, description="Execution weight")
    description: str = attribute(
        default="Model-cockpit action: route posture/intent, grant the model full agency over harness services and action tools in a think-act-observe loop."
    )

    router_model: str = attribute(default="gpt-4o-mini")
    router_model_action_type: str = attribute(default="")
    enable_canned_response: bool = attribute(default=True)
    enable_routing_cache: bool = attribute(default=False)

    model_action_type: str = attribute(default="AnthropicLanguageModelAction")
    model: str = attribute(default=COCKPIT_DEFAULT_SKILL_MODEL)
    skills: Any = attribute(default=None)
    denied_skills: List[str] = attribute(default_factory=list)
    skills_source: str = attribute(default="both")
    max_iterations: int = attribute(default=25)
    max_duration_seconds: float = attribute(default=300.0)
    strict_grounding: bool = attribute(default=True)
    response_mode: str = attribute(default="publish")

    converse_enabled: bool = attribute(default=True)
    converse_context_limit: int = attribute(default=2)
    converse_persona_prompt: str = attribute(
        default=(
            "Brief, in-character replies. Greetings and small talk: natural and short. "
            "Task-style asks: acknowledge and hand off to skills/tools."
        ),
    )

    history_limit: int = attribute(default=3)
    enable_accumulation: bool = attribute(default=True)

    router_model_temperature: float = attribute(default=0.1)
    router_model_max_tokens: int = attribute(default=400)
    canned_response_max_words: int = attribute(default=15)
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
    )
    confidence_threshold: float = attribute(default=0.7)
    enable_clarification: bool = attribute(default=False)
    max_fragment_buffer: int = attribute(default=5)

    model_temperature: float = attribute(default=0.3)
    model_max_tokens: int = attribute(default=8192)

    reasoning_budget_tokens: int = attribute(default=0)
    reasoning_enabled: Optional[bool] = attribute(default=None)
    reasoning_effort: Optional[str] = attribute(default=None)
    reasoning_extra: Optional[Dict[str, Any]] = attribute(default=None)

    # Unified streaming flag for internal progress (thoughts, reasoning, tool progress).
    # The legacy stream_thinking / stream_reasoning / stream_tool_progress fields are
    # accepted as deprecated aliases (see _resolve_stream_internal_progress below).
    stream_internal_progress: bool = attribute(default=True)
    stream_thinking: Optional[bool] = attribute(default=None)
    stream_reasoning: Optional[bool] = attribute(default=None)
    stream_tool_progress: Optional[bool] = attribute(default=None)

    max_concurrent_tools: int = attribute(default=5)
    tool_call_timeout: float = attribute(default=60.0)
    sanitize_tool_errors: bool = attribute(default=True)
    tool_servers: List[str] = attribute(default_factory=list)

    enable_skill_helper_tools: bool = attribute(default=True)
    enable_artifact_tools: bool = attribute(default=True)
    enable_cockpit_search: bool = attribute(default=True)
    tool_tier: str = attribute(default="standard")  # minimal | standard | full

    # Production-hygiene flags (Milestone G).
    # production_mode is an umbrella: when True it pulls the underlying flags
    # to safe defaults at config-build time (see _build_cockpit_config).
    production_mode: bool = attribute(default=False)
    block_raw_tool_invocation: bool = attribute(default=False)
    router_use_cockpit_search: bool = attribute(default=False)
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

    def _resolve_stream_internal_progress(self) -> bool:
        """Resolve the unified streaming flag, honoring legacy aliases.

        If any of the deprecated stream_thinking/stream_reasoning/stream_tool_progress
        fields are set explicitly (non-None), the unified flag is True iff *any* of
        them is True. Otherwise we use stream_internal_progress directly.
        """
        legacy = [
            self.stream_thinking,
            self.stream_reasoning,
            self.stream_tool_progress,
        ]
        explicit = [v for v in legacy if v is not None]
        if explicit:
            logger.debug(
                "CockpitInteractAction: stream_thinking/reasoning/tool_progress are "
                "deprecated; use stream_internal_progress instead."
            )
            return any(bool(v) for v in explicit)
        return bool(self.stream_internal_progress)

    def _build_cockpit_config(self) -> CockpitConfig:
        # Production-mode umbrella (Milestone G): forces hygiene flags on top
        # of operator settings. Operators who want a non-default mix should
        # leave production_mode=False and set the underlying flags directly.
        production = bool(self.production_mode)
        stream_internal = self._resolve_stream_internal_progress()
        enable_canned = bool(self.enable_canned_response)
        block_raw_tools = bool(self.block_raw_tool_invocation)
        if production:
            stream_internal = False
            enable_canned = False
            block_raw_tools = True

        return CockpitConfig(
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
            strict_grounding=self.strict_grounding,
            plan_first=self.plan_first,
            max_task_plan_steps=self.max_task_plan_steps,
            skills=self.skills,
            denied_skills=list(self.denied_skills or []),
            skills_source=self.skills_source,
            response_mode=self.response_mode,
            stream_internal_progress=stream_internal,
            production_mode=production,
            block_raw_tool_invocation=block_raw_tools,
            enable_skill_helper_tools=self.enable_skill_helper_tools,
            enable_artifact_tools=self.enable_artifact_tools,
            enable_cockpit_search=self.enable_cockpit_search,
            router_use_cockpit_search=self.router_use_cockpit_search,
            tool_tier=self.tool_tier,
            preload_user_memory=self.preload_user_memory,
            user_memory_max_chars=self.user_memory_max_chars,
            auto_track_tasks=self.auto_track_tasks,
            skill_index_inline_max_skills=self.skill_index_inline_max_skills,
            history_limit=self.history_limit,
            reasoning_budget_tokens=self.reasoning_budget_tokens,
            reasoning_enabled=self.reasoning_enabled,
            reasoning_effort=self.reasoning_effort,
            reasoning_extra=self.reasoning_extra,
            degenerate_response_max_chars=self.degenerate_response_max_chars,
            tool_servers=list(self.tool_servers or []),
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
        persona = await self.get_action("PersonaAction")
        agent = await self.get_agent()
        aid = getattr(agent, "id", None) or "unknown"
        if persona is None or not getattr(persona, "enabled", True):
            raise RuntimeError(
                f"CockpitInteractAction requires an enabled PersonaAction on agent '{aid}'."
            )
        desc = (getattr(persona, "persona_description", None) or "").strip()
        if not desc:
            raise RuntimeError(
                f"CockpitInteractAction requires non-empty PersonaAction.persona_description on agent '{aid}'."
            )
        return persona

    async def execute(self, visitor: InteractWalker) -> None:
        """Execute one cockpit step per walker visit.

        On first visit: route, set up engine, run first step.
        On revisits: restore engine state, run next step.
        On finalize-pending revisit (IA-only mode): deliver via persona using
        directives accumulated by upstream InteractActions, then clear state.

        When the model returns tool calls, persist state and re-add self to
        the walk path for the next visit. When the model produces a text
        response, deliver it and conclude the run.
        """
        if not self._ensure_interaction(visitor):
            await visitor.unrecord_action_execution()
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            await visitor.unrecord_action_execution()
            return

        visitor._skill_state = (
            visitor._skill_state if hasattr(visitor, "_skill_state") else {}
        )
        visitor._skill_state.setdefault("action", self)

        # Finalize-pending revisit (IA-only mode): cockpit was appended to the
        # end of the walk path to publish a persona-shaped response after
        # upstream IAs accumulated their directives.
        if visitor._skill_state.get(_COCKPIT_FINALIZE_PENDING_KEY):
            visitor._skill_state.pop(_COCKPIT_FINALIZE_PENDING_KEY, None)
            await self._finalize_via_persona(visitor)
            return

        engine = visitor._skill_state.get(_COCKPIT_ENGINE_KEY)

        # Stale-state guard: if the engine is from a different interaction,
        # clear it so routing runs on the fresh user message.
        stored_interaction_id = visitor._skill_state.get(_COCKPIT_INTERACTION_ID_KEY)
        if engine is not None and stored_interaction_id != interaction.id:
            logger.debug(
                "CockpitInteractAction: stale engine from interaction %s, "
                "current interaction %s — clearing and re-routing",
                stored_interaction_id,
                interaction.id,
            )
            visitor._skill_state.pop(_COCKPIT_ENGINE_KEY, None)
            visitor._skill_state.pop(_COCKPIT_STATE_KEY, None)
            visitor._skill_state.pop(_COCKPIT_INTERACTION_ID_KEY, None)
            engine = None

        if engine is None:
            # Fresh visit: Phase 1 (routing) + Phase 2 setup
            await self._phase_route_and_setup(visitor)
        else:
            # Revisit: skip routing, reuse engine, run next step
            await visitor.unrecord_action_execution()  # Avoid duplicate recording on revisit
            await self._phase_continue(visitor)

    async def _phase_route_and_setup(self, visitor: InteractWalker) -> None:
        """First visit: route, gate, dispatch to engine and/or interact_actions.

        Dispatch matrix (after posture + conversational gating):

        - ``routing.actions`` only          → cockpit engine path (existing)
        - ``routing.interact_actions`` only → skip engine, hand off to those IAs
        - both                              → engine first, IAs prepended on terminal
        - neither                           → cockpit engine path (engine handles via
          harness tools / model decides)
        """
        interaction = visitor.interaction

        try:
            from jvagent.action.cockpit.router import CockpitRouter

            # Canned lead-in is generated INSIDE the routing LLM call (see
            # ``ROUTING_CANNED_INSTRUCTIONS_TEMPLATE`` in router.py) and
            # published from CockpitRouter.route() before the engine starts.
            router = CockpitRouter(self)
            posture, routing = await router.route(visitor)

            if posture == "SUPPRESS":
                return

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

            # Resolve routed interact_actions and curate the walker queue so
            # only the cockpit + classified IAs + always_execute IAs remain.
            routed_ias = await resolve_routed_interact_actions(agent, routing)
            routed_ias = await filter_routed_interact_actions_by_access(
                agent, routed_ias, user_id=user_id, channel=channel
            )
            always_run_ias = await collect_always_execute_interact_actions(
                agent, exclude_class_names={self.__class__.__name__}
            )
            await curate_walk_path_for_cockpit(
                visitor,
                self,
                routed_ias,
                always_execute=always_run_ias,
            )

            if should_use_conversational_gate(
                routing, converse_enabled=self.converse_enabled
            ):
                await deliver_conversational(
                    self,
                    visitor,
                    response_mode=self.response_mode,
                    converse_persona_prompt=self.converse_persona_prompt,
                    converse_context_limit=self.converse_context_limit,
                )
                interaction.set_to_executed()
                return

            has_skills = bool(routing.actions)
            has_ias = bool(routed_ias)

            # interact_actions only → skip engine, hand off to IAs.
            # The curate above already placed the routed IAs in the walker queue
            # in weight order; the walker will visit them after cockpit returns.
            # We then append cockpit to the END of the walk path so it runs once
            # more after the IAs to invoke PersonaAction with the accumulated
            # directives — that's the user-facing response.
            if has_ias and not has_skills:
                logger.info(
                    "CockpitInteractAction: dispatching to interact_actions=%s "
                    "(skipping engine, scheduling persona finalize)",
                    [a.__class__.__name__ for a in routed_ias],
                )
                visitor._skill_state[_COCKPIT_FINALIZE_PENDING_KEY] = True
                try:
                    await visitor.append([self])
                except Exception as exc:
                    logger.warning(
                        "CockpitInteractAction: failed to append finalize step: %s",
                        exc,
                    )
                # Don't mark interaction executed yet — finalize step will do it.
                return

            # both → run engine; on terminal step, the IAs run after.
            if has_ias and has_skills:
                visitor._skill_state[_COCKPIT_PENDING_IAS_KEY] = routed_ias
                logger.info(
                    "CockpitInteractAction: dispatching to engine + queued "
                    "interact_actions=%s",
                    [a.__class__.__name__ for a in routed_ias],
                )

            # skills only OR neither → cockpit engine path.
            await self._start_cockpit(visitor, routing, persona)

        except Exception as exc:
            logger.warning(
                "CockpitInteractAction: error in phase_route_and_setup: %s",
                exc,
                exc_info=True,
            )
            await self._handle_error(visitor, exc)

    async def _phase_continue(self, visitor: InteractWalker) -> None:
        """Revisit: reuse engine instance and run next step."""
        engine = visitor._skill_state.get(_COCKPIT_ENGINE_KEY)
        if engine is None:
            logger.warning("CockpitInteractAction: revisit without engine, skipping")
            visitor._skill_state.pop(_COCKPIT_STATE_KEY, None)
            return

        try:
            step_result = await engine.step()
            await self._handle_step_result(visitor, engine, step_result)

        except Exception as exc:
            logger.warning(
                "CockpitInteractAction: error in phase_continue: %s",
                exc,
                exc_info=True,
            )
            await self._handle_error(visitor, exc)

    async def _start_cockpit(
        self,
        visitor: InteractWalker,
        routing: RoutingResult,
        persona: Any,
    ) -> None:
        """Set up the cockpit engine and run the first step."""
        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            return

        cfg = self._build_cockpit_config()
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

        if routing.actions:
            guidance = (
                f"\n\n[Router guidance] Intent: {routing.intent_type or 'UNCLEAR'}."
                f" Recommended skill(s): {', '.join(routing.actions)}."
                " Use the available tools to address this request."
            )
            if routing.interpretation:
                guidance = (
                    f"\n\n[Router guidance] Intent: {routing.intent_type or 'UNCLEAR'}."
                    f" Interpretation: {routing.interpretation}"
                    f" Recommended skill(s): {', '.join(routing.actions)}."
                    " Use the available tools to address this request."
                )
            agent_description += guidance

        model_action = await self.get_model_action(required=True)

        # Skill discovery for prompt construction.
        # Persists the resolved catalog and underlying discovered_skills dict
        # on visitor._skill_state so the engine, registry, and harness tools
        # (skill_*, cockpit_search) can all read the same source of truth.
        try:
            visitor_shim = CockpitVisitorShim(
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
            logger.warning(
                "CockpitInteractAction: skill discovery for prompt failed: %s", exc
            )

        visitor._skill_state["interact_walker"] = visitor

        ctx = CockpitContext(
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
            publish_callback=self._build_publish_callback(visitor),
        )

        engine = CockpitEngine(ctx)
        await engine.initialize()

        # Persist engine instance and interaction ID for revisit detection
        visitor._skill_state[_COCKPIT_ENGINE_KEY] = engine
        visitor._skill_state[_COCKPIT_INTERACTION_ID_KEY] = interaction.id

        step_result = await engine.step()
        await self._handle_step_result(visitor, engine, step_result)

    async def _handle_step_result(
        self,
        visitor: InteractWalker,
        engine: CockpitEngine,
        step_result: Any,
    ) -> None:
        """Process a step result: revisit for tool calls, deliver for final response."""
        interaction = visitor.interaction
        if not interaction:
            return

        status = getattr(step_result, "status", "")

        if status == "tool_calls":
            # Model called tools — persist state and revisit
            visitor._skill_state[_COCKPIT_STATE_KEY] = engine.save_state()
            # Check if response_publish set the finalized flag
            skill_state = visitor._skill_state
            if skill_state.get("cockpit_finalized"):
                # Tool already delivered the response — conclude
                skill_state.pop(_COCKPIT_STATE_KEY, None)
                skill_state.pop("cockpit_finalized", None)
                interaction.set_to_executed()
                return
            # Re-add self to walk path for next iteration
            await visitor.prepend([self])
            return

        # Terminal state: final_response, timeout, budget_exhausted, stuck
        visitor._skill_state.pop(_COCKPIT_STATE_KEY, None)
        visitor._skill_state.pop(_COCKPIT_ENGINE_KEY, None)
        visitor._skill_state.pop(_COCKPIT_INTERACTION_ID_KEY, None)
        visitor._skill_state.pop("cockpit_finalized", None)
        # Pending IAs are already in the walker queue from Phase 1 curate; the
        # walker visits them automatically after the cockpit revisit chain ends.
        # We pop the key here for observability hygiene only.
        pending_ias = visitor._skill_state.pop(_COCKPIT_PENDING_IAS_KEY, None) or []
        if pending_ias:
            logger.info(
                "CockpitInteractAction: engine done, walker will visit queued "
                "interact_actions=%s",
                [a.__class__.__name__ for a in pending_ias],
            )
        interaction.set_to_executed()

        final_response = getattr(step_result, "final_response", "") or ""

        if final_response.strip():
            # Build a CockpitResult-like object for delivery
            from jvagent.action.cockpit.context import CockpitResult

            result = CockpitResult(
                final_response=final_response,
                termination_reason=getattr(
                    step_result, "termination_reason", TerminationReason.COMPLETED
                ),
                iterations=getattr(step_result, "iterations", 0),
                duration_seconds=getattr(step_result, "duration_seconds", 0.0),
                activated_skills=getattr(step_result, "activated_skills", []),
            )

            skill_catalog = (visitor._skill_state or {}).get("skill_catalog")

            await deliver_final_response(
                self,
                visitor,
                result,
                response_mode=self.response_mode,
                degenerate_response_max_chars=self.degenerate_response_max_chars,
                skill_catalog=skill_catalog,
            )

    async def _finalize_via_persona(self, visitor: InteractWalker) -> None:
        """Run a persona-shaped delivery after upstream IAs queued directives.

        Used in "interact_actions only" mode. Cockpit was appended to the end
        of the walk path; this method invokes PersonaAction.respond() so the
        directives accumulated on the interaction are folded into the final
        user-facing response. PersonaAction itself reads directives from
        ``visitor.interaction``.
        """
        interaction = visitor.interaction
        if not interaction:
            await visitor.unrecord_action_execution()
            return

        try:
            persona = await self._require_persona()
        except Exception as exc:
            logger.warning(
                "CockpitInteractAction: persona unavailable for finalize step: %s",
                exc,
            )
            interaction.set_to_executed()
            return

        try:
            # PersonaAction.respond(interaction, visitor=...) reads
            # interaction.directives + parameters and publishes via the
            # response bus (when visitor provided).
            await persona.respond(interaction, visitor=visitor)
        except Exception as exc:
            logger.warning(
                "CockpitInteractAction: persona finalize delivery failed: %s",
                exc,
                exc_info=True,
            )
        finally:
            interaction.set_to_executed()

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

    async def _handle_error(self, visitor: InteractWalker, exc: Exception) -> None:
        """Handle errors during execution."""
        visitor._skill_state.pop(_COCKPIT_ENGINE_KEY, None)
        visitor._skill_state.pop(_COCKPIT_STATE_KEY, None)
        visitor._skill_state.pop(_COCKPIT_INTERACTION_ID_KEY, None)
        interaction = visitor.interaction
        if interaction:
            if not interaction.response:
                interaction.response = (
                    "I encountered an error processing your request. Please try again."
                )
                try:
                    await interaction.save()
                except Exception:
                    pass
        await visitor.unrecord_action_execution()

    async def healthcheck(self) -> bool:
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        agent = await self.get_agent()
        if not agent:
            return True
        persona = await self.get_action("PersonaAction")
        if not persona or not getattr(persona, "enabled", True):
            return False
        if not (getattr(persona, "persona_description", None) or "").strip():
            return False
        return True
