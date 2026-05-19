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

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.action.cockpit.catalog.skill_discovery import (
    list_always_active_skill_names,
)
from jvagent.action.cockpit.config import CockpitConfig
from jvagent.action.cockpit.context import CockpitContext
from jvagent.action.cockpit.contracts import TerminationReason
from jvagent.action.cockpit.delivery.delegation import (
    collect_always_execute_interact_actions,
    curate_walk_path_for_cockpit,
    prepend_routed_interact_actions,
    resolve_routed_interact_actions,
)
from jvagent.action.cockpit.delivery.gates import (
    CONVERSE_SKILL_NAMES,
    should_use_conversational_gate,
)
from jvagent.action.cockpit.delivery.helpers import (
    deliver_conversational,
    deliver_final_response,
)
from jvagent.action.cockpit.delivery.persona_delivery import deliver_via_persona
from jvagent.action.cockpit.engine import CockpitEngine
from jvagent.action.cockpit.registry.access import (
    filter_routed_interact_actions_by_access,
    filter_routed_skills_by_access,
)
from jvagent.action.cockpit.registry.shim import CockpitVisitorShim
from jvagent.action.cockpit.routing.types import POSTURE_RESPOND, RoutingResult
from jvagent.action.cockpit.session import (
    CockpitSession,
    clear_session,
    get_session,
    get_session_optional,
)
from jvagent.action.interact.base import InteractAction

if TYPE_CHECKING:
    from jvagent.action.cockpit.routing.router import CockpitRouter
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

COCKPIT_DEFAULT_SKILL_MODEL: str = "claude-sonnet-4-20250514"

# Cockpit-owned per-run state (engine reference, dispatch flags, etc.)
# lives on a single ``CockpitSession`` object accessed via ``get_session``.
# See ``jvagent.action.cockpit.session`` for the field catalog and lifecycle.


def _routing_clarification_fallbacks_default() -> List[str]:
    from jvagent.action.cockpit.routing.router import (
        ROUTING_CLARIFICATION_FALLBACK_MESSAGES,
    )

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

    model_action_type: str = attribute(default="AnthropicLanguageModelAction")
    model: str = attribute(default=COCKPIT_DEFAULT_SKILL_MODEL)
    skills: Any = attribute(default=None)
    denied_skills: List[str] = attribute(default_factory=list)
    skills_source: str = attribute(default="both")
    max_iterations: int = attribute(default=25)
    max_duration_seconds: float = attribute(default=300.0)
    max_dynamic_activations: int = attribute(default=10)
    response_mode: str = attribute(default="publish")

    converse_enabled: bool = attribute(default=True)
    converse_context_limit: int = attribute(default=2)
    converse_persona_prompt: str = attribute(
        default=(
            "Brief, in-character replies. Greetings and small talk: natural and short. "
            "Task-style asks: acknowledge and hand off to skills/tools."
        ),
    )
    # When True, bypass the cockpit engine and reply via PersonaAction whenever
    # the router recommends no skills and no interact_actions (in addition to
    # the strict CONVERSATIONAL-intent path). Saves a heavy engine LLM call on
    # greetings, smalltalk, and any utterance the router classifies as having
    # no work to do. Set False to fall through to the engine for UNCLEAR /
    # INFORMATIONAL intents that have no specific handler — useful when the
    # engine's harness tools (memory, artifacts, conversation search) should
    # still get a chance to act.
    conversational_fast_path: bool = attribute(default=True)

    history_limit: int = attribute(default=3)
    max_statement_length: Optional[int] = attribute(default=None)
    enable_accumulation: bool = attribute(default=True)

    router_model_temperature: float = attribute(default=0.1)
    router_model_max_tokens: int = attribute(default=400)
    canned_response_max_words: int = attribute(default=15)
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
    )

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
    enable_cockpit_search: bool = attribute(default=True)
    tool_tier: str = attribute(default="standard")  # minimal | standard | full

    # Phase 1 latency knobs.
    # ``enable_router_preclassifier``: cheap local heuristic that fires
    # before the router LLM call. When the utterance is unambiguous
    # smalltalk (greeting / thanks / goodbye / pleasantry) and no active
    # task is in flight, the router synthesises a converse-skill route
    # and skips the LLM round-trip. See routing/preclassifier.py.
    # ``enable_interact_router_cache``: opt into the in-process router
    # cache. Cache keys fold active-task fingerprints so fragments routed
    # mid-interview don't share keys with the same fragment after the
    # interview completes. TTL is governed by perf config
    # ``interact_router_cache_ttl`` (default 45s).
    enable_router_preclassifier: bool = attribute(default=True)
    enable_interact_router_cache: bool = attribute(default=False)

    # Hygiene flags. Each one is independently tunable; there is no umbrella
    # toggle. ``block_raw_tool_invocation`` defends the engine prompt against
    # users naming tools by name; the other three keep the loop predictable.
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

    # Overridable prompt templates (mirrors PersonaAction.system_prompt pattern).
    # Defaults are empty strings — engine falls back to module-level constants
    # in cockpit/prompts.py when the override is blank.  Set in agent.yaml to
    # customise engine behaviour without forking the framework.
    system_prompt: str = attribute(default="")
    task_planning_prompt: str = attribute(default="")
    security_prompt: str = attribute(default="")
    capability_search_prompt: str = attribute(default="")
    citation_instruction: str = attribute(default="")

    def _build_cockpit_config(self) -> CockpitConfig:
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
            enable_cockpit_search=self.enable_cockpit_search,
            max_dynamic_activations=self.max_dynamic_activations,
            router_use_cockpit_search=self.router_use_cockpit_search,
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

        if not hasattr(visitor, "_skill_state"):
            visitor._skill_state = {}
        visitor._skill_state.setdefault("action", self)

        session = get_session(visitor)

        # Finalize-pending revisit (IA-only mode): cockpit was appended to the
        # end of the walk path to publish a persona-shaped response after
        # upstream IAs accumulated their directives. This visit is purely a
        # delivery shim — unrecord so the action trace shows cockpit only
        # once (the meaningful Phase 1 visit), with PersonaAction following
        # naturally as the renderer.
        if session.ia_finalize_pending:
            session.ia_finalize_pending = False
            await visitor.unrecord_action_execution()
            await self._finalize_via_persona(visitor)
            return

        # Stale-state guard: if the engine is from a different interaction,
        # reset the session so routing runs on the fresh user message.
        if session.engine is not None and session.interaction_id != interaction.id:
            logger.debug(
                "CockpitInteractAction: stale engine from interaction %s, "
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
            from jvagent.action.cockpit.routing.router import CockpitRouter

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
            # Always-execute IAs MUST also go through per-user access
            # control, otherwise they sit in the curated queue until
            # ``enforce_interact_action_access`` denies them at visit
            # time — wasting observability slots and emitting
            # ``deny_access_directive`` for every turn. AUDIT-interact
            # HIGH-08.
            always_run_ias = await filter_routed_interact_actions_by_access(
                agent, always_run_ias, user_id=user_id, channel=channel
            )
            await curate_walk_path_for_cockpit(
                visitor,
                self,
                routed_ias,
                always_execute=always_run_ias,
            )

            if should_use_conversational_gate(
                routing,
                converse_enabled=self.converse_enabled,
                conversational_fast_path=self.conversational_fast_path,
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
            session = get_session(visitor)

            if has_ias and not has_skills:
                logger.info(
                    "CockpitInteractAction: dispatching to interact_actions=%s "
                    "(skipping engine, scheduling persona finalize)",
                    [a.__class__.__name__ for a in routed_ias],
                )
                session.ia_finalize_pending = True
                finalize_enqueued = False
                try:
                    await visitor.append([self])
                    # WalkerQueue.append silently drops when the queue is at
                    # its ``max_queue_size`` cap. Verify the cockpit instance
                    # actually landed in the tail before returning — otherwise
                    # the finalize step never runs and the user sees no
                    # response. AUDIT-interact-cockpit CRIT-04.
                    try:
                        current_queue = await visitor.get_queue()
                        finalize_enqueued = any(
                            getattr(node, "id", None) == self.id
                            for node in current_queue
                        )
                    except Exception:
                        # If queue introspection isn't available, assume the
                        # append succeeded — the fallback below only fires on
                        # an explicit miss.
                        finalize_enqueued = True
                except Exception as exc:
                    logger.warning(
                        "CockpitInteractAction: failed to append finalize step: %s",
                        exc,
                    )

                if not finalize_enqueued:
                    logger.warning(
                        "CockpitInteractAction: finalize append dropped "
                        "(walker queue at cap); falling back to inline finalize"
                    )
                    session.ia_finalize_pending = False
                    # Inline persona finalize so the turn is not silently
                    # dropped. Mark the interaction executed afterwards.
                    try:
                        await deliver_via_persona(
                            self,
                            visitor,
                            response_mode="respond",
                            history_limit=self.history_limit,
                        )
                    except Exception as deliver_exc:
                        logger.warning(
                            "CockpitInteractAction: inline finalize failed: %s",
                            deliver_exc,
                        )
                    try:
                        interaction.set_to_executed()
                    except Exception:
                        pass
                # Don't mark interaction executed yet — finalize step will do it.
                return

            # both → run engine; on terminal step, the IAs run after.
            if has_ias and has_skills:
                session.pending_interact_actions = routed_ias
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
                "CockpitInteractAction: revisit without engine; "
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
            return

        # AUDIT-interact HIGH-02: enforce a per-interaction step cap that
        # survives engine rebuilds (engine._iteration resets to 0 on rebuild).
        session.total_steps_this_interaction = (
            session.total_steps_this_interaction or 0
        ) + 1
        if session.total_steps_this_interaction > max(1, int(self.max_iterations) * 2):
            logger.warning(
                "CockpitInteractAction: per-interaction step cap exceeded "
                "(%d steps; max_iterations=%d, ceiling=2x). Terminating turn.",
                session.total_steps_this_interaction,
                self.max_iterations,
            )
            await self._handle_error(
                visitor,
                RuntimeError(
                    "Per-interaction cockpit step cap exceeded — turn terminated"
                ),
            )
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

        # The converse skill is a routing alias — it has no tools and no
        # engine workflow. If the engine is starting, the gate already let
        # this through because OTHER skills are also routed; surfacing
        # converse in the engine's preloaded list would be incoherent
        # context. Strip it from the engine's view (catalog still has it).
        preloaded = [s for s in preloaded if s not in CONVERSE_SKILL_NAMES]

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
            routed_skills=list(routing.actions or []),
            publish_callback=self._build_publish_callback(visitor),
        )

        engine = CockpitEngine(ctx)
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
        engine: CockpitEngine,
        step_result: Any,
    ) -> None:
        """Process a step result: revisit for tool calls, deliver for final response."""
        interaction = visitor.interaction
        if not interaction:
            return

        session = get_session(visitor)
        status = getattr(step_result, "status", "")

        if status == "tool_calls":
            # Model called tools — persist state and revisit
            session.debug_state = engine.save_state()
            # Check if response_publish set the finalized flag
            if session.finalized:
                # Tool already delivered the response — conclude
                session.reset()
                interaction.set_to_executed()
                return
            # Re-add self to walk path for next iteration
            await visitor.prepend([self])
            return

        # Terminal state: final_response, timeout, budget_exhausted, stuck.
        # Pending IAs are already in the walker queue from Phase 1 curate; the
        # walker visits them automatically after the cockpit revisit chain ends.
        # We log them for observability before resetting the session.
        pending_ias = list(session.pending_interact_actions)
        if pending_ias:
            logger.info(
                "CockpitInteractAction: engine done, walker will visit queued "
                "interact_actions=%s",
                [a.__class__.__name__ for a in pending_ias],
            )
        session.reset()
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
        of the walk path; this routes through the unified ``deliver_via_persona``
        in ``"respond"`` mode (no content, no directive — directives accumulated
        by upstream IAs are read straight off the interaction by PersonaAction).
        """
        interaction = visitor.interaction
        if not interaction:
            await visitor.unrecord_action_execution()
            return

        try:
            await self._require_persona()
        except Exception as exc:
            logger.warning(
                "CockpitInteractAction: persona unavailable for finalize step: %s",
                exc,
            )
            interaction.set_to_executed()
            return

        try:
            await deliver_via_persona(
                self,
                visitor,
                content=None,
                response_mode="respond",
                history_limit=(
                    max(1, int(self.history_limit)) if self.history_limit else 4
                ),
                use_history=True,
            )
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
        clear_session(visitor)
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
        persona: Any = await self.get_action("PersonaAction")
        if not persona or not getattr(persona, "enabled", True):
            return False
        if not (getattr(persona, "persona_description", None) or "").strip():
            return False
        return True

    @classmethod
    async def refresh_skills(cls, visitor: InteractWalker) -> List[str]:
        """Re-discover skills and merge any newly installed bundles into the live session.

        The cockpit assembles its tool registry fresh on every step from
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
        """Hot-unload *skill_name* from the current cockpit session."""
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
