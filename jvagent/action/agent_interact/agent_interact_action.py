"""AgentInteractAction: unified skill-routing interact action.

Fuses the InteractRouter and SkillInteractAction into a single InteractAction
with a single walker visit.  Architecture:

Phase 1 — Route (``AgentInteractRouter``):
    ``router_model`` + optional ``router_model_action_type`` (falls back to
    ``model_action_type``). Register one LM action per provider (e.g. OpenAI + Ollama).

Phase 2 — Execute (requires enabled ``PersonaAction`` on the agent):
    2a: Conversational path — ``response_mode`` ``publish`` uses ``PersonaAction.respond_slim``
    (``system`` = ``persona_description`` only); ``respond`` adds a directive and calls
    ``PersonaAction`` via ``respond(visitor)``.
    2b: Skill loop — ``model_action_type`` (agentic think-act-observe); final delivery
    mirrors the same ``publish`` vs ``respond`` split.

Legacy InteractRouter + SkillInteractAction remain available for backward
compatibility; this class takes precedence when declared in agent.yaml.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from jvspatial.core.annotations import attribute

from jvagent.action.agent_interact.router import AgentInteractRouter
from jvagent.action.agent_interact.router.gates import should_use_conversational_gate
from jvagent.action.agent_interact.router.prompts import (
    ROUTING_CANNED_INSTRUCTIONS_TEMPLATE,
    ROUTING_CLARIFICATION_FALLBACK_MESSAGES,
    ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE,
    ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE,
    ROUTING_PRIOR_FRAGMENTS_SECTION,
    ROUTING_SYSTEM_PROMPT,
    ROUTING_USER_PROMPT_TEMPLATE,
)
from jvagent.action.agent_interact.skill.agentic_loop import (
    AgentInteractSkillAction,
    run_agentic_skill_loop,
)
from jvagent.action.agent_interact.skill.always_active import (
    list_always_active_skill_names,
)
from jvagent.action.agent_interact.skill.context import AgentInteractSkillRunContext
from jvagent.action.agent_interact.skill.contracts import DEFAULT_SKILL_MODEL
from jvagent.action.agent_interact.skill.converse_delivery import (
    deliver_conversational_turn,
)
from jvagent.action.agent_interact.skill.hot_reload import (
    refresh_skills as _refresh_skills_impl,
)
from jvagent.action.agent_interact.skill.hot_reload import (
    remove_skill as _remove_skill_impl,
)
from jvagent.action.agent_interact.skill.run_config import (
    build_skill_run_config,
)
from jvagent.action.agent_interact.skill.shim import AgentInteractVisitorShim
from jvagent.action.interact.base import InteractAction
from jvagent.action.persona.persona_action import PersonaAction
from jvagent.action.router.routing_result import POSTURE_RESPOND, RoutingResult
from jvagent.action.skill.skill_catalog import SkillCatalog

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)

# Matches ToolExecutor's canonical empty-result line (see ``tool_executor.py``).
_EMPTY_TOOL_RESULT_LINE_RE = re.compile(r"^Tool `[^`]+` returned empty output\.\s*$")


def _skill_loop_output_is_deliverable(text: Optional[str]) -> bool:
    """True when skill-loop output is substantive (not blank / empty-tool-only)."""
    raw = text or ""
    if not raw.strip():
        return False
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return False
    if all(_EMPTY_TOOL_RESULT_LINE_RE.match(ln) for ln in lines):
        return False
    return True


def _routing_clarification_fallbacks_default() -> List[str]:
    return list(ROUTING_CLARIFICATION_FALLBACK_MESSAGES)


class AgentInteractAction(InteractAction):
    """Unified skill-routing interact action — single walker visit.

    Replaces the legacy InteractRouter + SkillInteractAction pair with a
    single action that routes posture/intent, handles conversational banter
    on the converse fast path, and executes skill-based tasks in an agentic
    think-act-observe loop with mid-loop skill discovery.
    """

    weight: int = attribute(default=-200, description="Execution weight")
    description: str = attribute(
        default="Unified skill-routing action: route posture/intent, converse fast path for casual chat, execute skills in an agentic loop."
    )

    # ═══════════════════════════════════════════════════════════════════
    # Tier 1 — Core: set these in every agent.yaml
    # ═══════════════════════════════════════════════════════════════════

    # ── Router ──
    router_model: str = attribute(
        default="gpt-4o-mini", description="Model id for routing LLM call"
    )
    router_model_action_type: str = attribute(
        default="",
        description="LM action class name for router (empty → model_action_type)",
    )
    enable_canned_response: bool = attribute(
        default=True, description="Publish brief canned ack before loop"
    )
    enable_routing_cache: bool = attribute(
        default=False, description="Cache routing decisions per session"
    )
    routing_system_prompt: str = attribute(
        default=ROUTING_SYSTEM_PROMPT,
        description="Routing LLM system prompt (defaults in router/prompts.py)",
    )
    routing_user_prompt_template: str = attribute(
        default=ROUTING_USER_PROMPT_TEMPLATE,
        description=(
            "Routing user prompt template; placeholders: utterance, skills_json, "
            "interact_actions_json, active_tasks_section, history_section, "
            "prior_fragments_section, optional_instructions, entity_field, canned_field"
        ),
    )
    routing_prior_fragments_section: str = attribute(
        default=ROUTING_PRIOR_FRAGMENTS_SECTION,
        description="Template for prior deferred fragments block; placeholder: fragments_list",
    )
    routing_canned_instructions_template: str = attribute(
        default=ROUTING_CANNED_INSTRUCTIONS_TEMPLATE,
        description="Appended routing rule #6 for canned_response; placeholders: skip_intents, max_words",
    )
    routing_clarification_user_prompt_template: str = attribute(
        default=ROUTING_CLARIFICATION_USER_PROMPT_TEMPLATE,
        description=(
            "Primary clarification user prompt (low-confidence branch); placeholders: "
            "utterance, interpretation, intent_type, confidence, issues. Empty string skips this step."
        ),
    )
    routing_clarification_paraphrase_prompt_template: str = attribute(
        default=ROUTING_CLARIFICATION_PARAPHRASE_PROMPT_TEMPLATE,
        description="User prompt when paraphrasing a clarification fallback; placeholders: utterance, template",
    )
    routing_clarification_fallback_messages: List[str] = attribute(
        default_factory=_routing_clarification_fallbacks_default,
        description="Fallback clarification strings before paraphrase LLM call",
    )

    # ── Conversational branch (PersonaAction) ──
    converse_enabled: bool = attribute(
        default=True,
        description="Enable conversational branch (requires PersonaAction)",
    )

    # ── Skill loop ──
    model_action_type: str = attribute(
        default="AnthropicLanguageModelAction",
        description="LM action class name for agentic loop",
    )
    model: str = attribute(
        default=DEFAULT_SKILL_MODEL, description="Model id for agentic loop"
    )
    skills: Any = attribute(
        default=None, description="Skill selector: list of names/globs, '-all', or None"
    )
    denied_skills: List[str] = attribute(
        default_factory=list, description="Names/globs to exclude from skill bundles"
    )
    skills_source: str = attribute(
        default="both", description="Skill source: builtin | app | both | none"
    )
    max_iterations: int = attribute(
        default=25, description="Hard cap on think-act-observe cycles"
    )
    strict_grounding: bool = attribute(
        default=True, description="Enforce grounding-focused prompting"
    )
    response_mode: str = attribute(
        default="publish",
        description=(
            "Final delivery for conversational turns and skill results: 'publish' = "
            "PersonaAction.respond_slim (system = persona_description only); "
            "'respond' = full Persona via directives + respond(visitor). Requires PersonaAction."
        ),
    )

    # ── Shared ──
    history_limit: int = attribute(
        default=3, description="Prior interactions included in context"
    )
    enable_accumulation: bool = attribute(
        default=True, description="Enable DEFER posture + fragment buffer"
    )

    # ═══════════════════════════════════════════════════════════════════
    # Tier 2 — Advanced: tune only when the defaults aren't suitable
    # ═══════════════════════════════════════════════════════════════════

    # ── Router tuning ──
    router_model_temperature: float = attribute(
        default=0.1, description="Temperature for routing LLM"
    )
    router_model_max_tokens: int = attribute(
        default=400, description="Max tokens for routing LLM"
    )
    canned_response_max_words: int = attribute(
        default=8, description="Max words for canned response"
    )
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        description="Intent types that skip canned response",
    )
    confidence_threshold: float = attribute(
        default=0.7, description="Min confidence to proceed"
    )
    enable_clarification: bool = attribute(default=False)
    max_fragment_buffer: int = attribute(
        default=5, description="Max deferred fragments"
    )
    exceptions: List[str] = attribute(
        default_factory=list, description="Action names that always execute"
    )
    pass_through_task_types: Sequence[str] = attribute(
        default=("INTERVIEW",), description="Task types that skip router LLM"
    )
    pass_through_when_media: bool = attribute(
        default=True, description="Skip router when media attached"
    )
    bypass_canned_response: str = attribute(
        default="One moment", description="Canned response for bypass paths"
    )
    media_bypass_actions: List[str] = attribute(
        default_factory=list, description="Route to these actions when media attached"
    )

    # ── Conversational branch tuning ──
    converse_context_limit: int = attribute(
        default=2, description="Prior interactions in conv context"
    )
    converse_persona_prompt: str = attribute(
        default=(
            "Brief, in-character replies. Greetings and small talk: natural and short. "
            "Task-style asks: acknowledge and hand off to skills/tools."
        ),
        description=(
            "Short sub-prompt used as the Persona directive when response_mode=respond on "
            "conversational turns (utterance/history are already in Persona scaffolding—do not "
            "repeat them here)."
        ),
    )

    # ── Skill loop tuning ──
    model_temperature: float = attribute(default=0.3)
    model_max_tokens: int = attribute(default=8192)
    max_duration_seconds: float = attribute(default=300.0)
    reasoning_budget_tokens: int = attribute(default=0)
    reasoning_enabled: Optional[bool] = attribute(default=None)
    reasoning_effort: Optional[str] = attribute(default=None)
    reasoning_extra: Optional[Dict[str, Any]] = attribute(default=None)
    mirror_assistant_stream_as_thoughts: Optional[bool] = attribute(default=None)
    stream_thinking: bool = attribute(default=True)
    stream_reasoning: bool = attribute(default=True)
    stream_tool_progress: bool = attribute(default=True)
    commit_intermediate_messages: bool = attribute(default=True)
    relay_thoughts_to_channels: bool = attribute(default=False)
    max_full_tool_results: int = attribute(default=10)
    max_tool_result_tokens: int = attribute(default=400)
    tool_result_truncation_chars: int = attribute(default=500)
    call_timeout_seconds: float = attribute(default=60.0)
    enable_skill_helper_tools: bool = attribute(default=True)
    skill_index_inline_max_skills: int = attribute(default=5)
    max_skill_activations: int = attribute(default=8)
    max_iterations_per_skill: int = attribute(default=0)
    max_duration_per_skill_seconds: float = attribute(default=0.0)
    semantic_skill_search: bool = attribute(default=False)
    skill_first_retry_limit: int = attribute(default=1)
    skill_first_retry_min_relevance: float = attribute(default=0.25)
    prioritize_skills_first: bool = attribute(default=True)
    tool_servers: List[str] = attribute(default_factory=list)
    allow_local_tools: bool = attribute(default=False)
    local_tools_path: Optional[str] = attribute(default=None)
    plan_first: bool = attribute(default=True)
    final_review: bool = attribute(default=True)
    final_review_max_plan_steps: Optional[int] = attribute(default=None)
    task_nudge_retry_limit: int = attribute(default=2)
    max_total_task_nudges: int = attribute(default=6)
    max_task_plan_steps: int = attribute(default=50)
    stuck_detection_window: int = attribute(default=3)
    stuck_intent_jaccard_threshold: float = attribute(default=0.7)
    max_midcourse_corrections: int = attribute(default=2)
    progress_check_interval: int = attribute(default=5)
    enable_checkpoints: bool = attribute(default=True)
    enable_evidence_log: bool = attribute(default=True)

    # ------------------------------------------------------------------
    # Language model resolution (multi-provider)
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_model_action_type(value: Any) -> Optional[str]:
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    def _language_model_action_type_for_purpose(self, purpose: str) -> Optional[str]:
        """Resolve which registered LanguageModelAction class to use per pipeline phase."""
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
        """Return the LanguageModelAction for *purpose* (``router`` or ``skill``).

        ``router_model_action_type`` falls back to ``model_action_type`` when unset.

        Args:
            required: If True, raise when no LM action can be resolved.
            purpose: ``skill`` (agentic loop) or ``router`` (``AgentInteractRouter``).
        """
        from jvagent.action.model.language.base import LanguageModelAction

        type_name = self._language_model_action_type_for_purpose(purpose)
        model_action: Optional[LanguageModelAction]
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
            label = (
                type_name
                or getattr(self, "model_action_type", None)
                or "LanguageModelAction"
            )
            raise RuntimeError(
                f"Model action for purpose '{purpose}' (type '{label}') not found for agent '{agent_id}'"
            )
        return None

    async def _require_persona_for_interact(self) -> PersonaAction:
        """Return enabled PersonaAction or raise (AgentInteract hard-depends on Persona)."""
        persona = await self.get_action(PersonaAction)
        agent = await self.get_agent()
        aid = getattr(agent, "id", None) or "unknown"
        if persona is None or not getattr(persona, "enabled", True):
            raise RuntimeError(
                f"AgentInteractAction requires an enabled PersonaAction on agent '{aid}'. "
                "Add `action: jvagent/persona` to agent.yaml (see examples/jvagent_app/agents/jvagent/unified_agent)."
            )
        desc = (getattr(persona, "persona_description", None) or "").strip()
        if not desc:
            raise RuntimeError(
                f"AgentInteractAction requires non-empty PersonaAction.persona_description "
                f"on agent '{aid}'."
            )
        return persona

    @staticmethod
    def _persona_description_text(persona: PersonaAction) -> str:
        return (getattr(persona, "persona_description", None) or "").strip()

    async def _deliver_slim_persona_publish(
        self,
        visitor: "InteractWalker",
        *,
        user_prompt: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Delegate slim delivery to ``PersonaAction.respond_slim``."""
        persona = await self._require_persona_for_interact()
        interaction = visitor.interaction
        if not interaction:
            return
        await persona.respond_slim(
            interaction,
            visitor,
            prompt=user_prompt,
            history=history or [],
        )

    # ------------------------------------------------------------------
    # InteractAction entry point
    # ------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        """Single walker visit: Phase 1 Route → Phase 2 Execute.

        Args:
            visitor: The InteractWalker visiting this action.
        """
        if not self._ensure_interaction(visitor):
            await visitor.unrecord_action_execution()
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            logger.warning("AgentInteractAction: No interaction or conversation")
            await visitor.unrecord_action_execution()
            return

        # Wire skill_state for hot-reload + store as cache for router
        visitor._skill_state = (
            visitor._skill_state if hasattr(visitor, "_skill_state") else {}
        )
        visitor._skill_state.setdefault("action", self)

        # ── Phase 1: Route ──
        router = AgentInteractRouter(self)
        posture, routing = await router.route(visitor)

        if posture == "SUPPRESS" or posture == "DEFER":
            return  # walk path already cleared by router

        if routing is None:
            routing = RoutingResult(posture=POSTURE_RESPOND)

        try:
            await self._require_persona_for_interact()
        except RuntimeError as exc:
            logger.error("AgentInteractAction: %s", exc)
            await visitor.unrecord_action_execution()
            raise

        # ── Phase 2: Execute (conversational gate vs processing gate) ──
        if should_use_conversational_gate(
            routing, converse_enabled=self.converse_enabled
        ):
            await self._phase_execute_conversational(visitor)
        else:
            await self._phase_execute_skill_loop(visitor, routing)

    # ------------------------------------------------------------------
    # Phase 2a: Converse fast path
    # ------------------------------------------------------------------

    async def _phase_execute_conversational(self, visitor: "InteractWalker") -> None:
        conversation = visitor.conversation
        interaction = visitor.interaction
        if not conversation or not interaction:
            logger.warning(
                "AgentInteractAction: conversational path missing conversation"
            )
            return

        await deliver_conversational_turn(self, visitor)

    # ------------------------------------------------------------------
    # Phase 2b: Agentic skill loop
    # ------------------------------------------------------------------

    async def _phase_execute_skill_loop(
        self, visitor: "InteractWalker", routing: RoutingResult
    ) -> None:
        """Run the agentic skill loop with router-selected preloaded skills.

        Skills the router selected are pre-registered on the ToolExecutor.
        The model may call skill_search / list_skills mid-loop to discover
        and activate additional skills from the full catalog.
        """
        interaction = visitor.interaction
        conversation = visitor.conversation
        if not interaction or not conversation:
            return

        try:
            model_action = await self.get_model_action(required=True)
            agent = getattr(visitor, "_agent", None)

            cfg = build_skill_run_config(self)

            persona = await self._require_persona_for_interact()
            agent_name = getattr(persona, "persona_name", "Agent")
            agent_description = getattr(
                persona,
                "persona_description",
                "An intelligent skills-based agent.",
            )

            try:
                visitor_shim = AgentInteractVisitorShim(
                    agent,
                    None,  # action_resolver (built below)
                    user_id=getattr(visitor, "user_id", None),
                    conversation=conversation,
                    interaction=interaction,
                    session_id=visitor.session_id,
                    response_bus=visitor.response_bus,
                    channel=getattr(visitor, "channel", None),
                )
                await SkillCatalog.discover(
                    visitor=visitor_shim,
                    skills_selector=cfg.skills,
                    skills_source=cfg.skills_source,
                    denied_skills=cfg.denied_skills or None,
                )
            except Exception as exc:
                logger.warning(
                    "AgentInteractAction: skill discovery for persona prompt failed: %s",
                    exc,
                )

            # ── Publish callback ──
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

            # ── Determine preloaded skills (router-selected + always_active) ──
            preloaded = list(routing.actions)
            always_active_skills = await self._get_always_active_skills(
                agent, conversation
            )
            for name in always_active_skills:
                if name not in preloaded:
                    preloaded.append(name)

            visitor._skill_state["interact_walker"] = visitor

            # Build AgentInteract-only run context (preload names are not on shared SkillRunContext).
            ctx = AgentInteractSkillRunContext(
                utterance=visitor.utterance or "",
                conversation=conversation,
                interaction=interaction,
                model_action=model_action,
                task_service=visitor.tasks,
                config=cfg,
                agent=agent,
                response_bus=visitor.response_bus,
                session_id=visitor.session_id,
                channel=getattr(visitor, "channel", None),
                stream=getattr(visitor, "stream", False),
                user_id=getattr(visitor, "user_id", None),
                publish_callback=_publish_cb,
                agent_name=agent_name,
                agent_description=agent_description,
                skill_state=visitor._skill_state,
                preloaded_skills=preloaded,
            )

            result = await run_agentic_skill_loop(ctx)

            # Mark interaction as executed
            interaction.set_to_executed()

            # Deliver final response (no user-facing reply if nothing substantive came back)
            if not _skill_loop_output_is_deliverable(result.final_response):
                logger.info(
                    "AgentInteractAction: skipping delivery; empty or empty-tool-only skill output"
                )
                return

            if result.final_response:
                skill_catalog = (visitor._skill_state or {}).get("skill_catalog")
                effective_mode = self._normalize_effective_response_mode(
                    self._resolve_response_mode_from_result(
                        result, skill_catalog=skill_catalog
                    )
                )
                degenerate = AgentInteractSkillAction._is_degenerate_response(
                    result.final_response,
                    max_chars=cfg.degenerate_response_max_chars,
                )

                # Verbatim delivery paths (skip unconstrained Persona polish)
                _verbatim = False
                if skill_catalog is not None:
                    activated = set(getattr(result, "activated_skills", []) or [])
                    _verbatim = skill_catalog.get_verbatim_final_override(activated)

                if _verbatim and not degenerate:
                    logger.info(
                        "AgentInteractAction: delivering skill output verbatim "
                        "(verbatim-final or final_review_exercised with polish disabled)"
                    )
                    await self.publish(
                        visitor,
                        content=result.final_response,
                        streaming_complete=True,
                    )
                    return

                if effective_mode == "respond" and not degenerate:
                    await visitor.add_directive(
                        self._format_persona_directive(result.final_response)
                    )
                    await self.respond(visitor)
                elif effective_mode == "respond" and degenerate:
                    logger.warning(
                        "AgentInteractAction: skipping Persona; degenerate response; "
                        "publishing raw skill output"
                    )
                    await self.publish(
                        visitor,
                        content=result.final_response,
                        streaming_complete=True,
                    )
                elif degenerate:
                    await self.publish(
                        visitor,
                        content=result.final_response,
                        streaming_complete=True,
                    )
                else:
                    await self._deliver_slim_persona_publish(
                        visitor,
                        user_prompt=result.final_response,
                        history=[],
                    )

        except Exception as exc:
            logger.error(
                "AgentInteractAction: Error during agentic loop: %s", exc, exc_info=True
            )
            await visitor.unrecord_action_execution()

    # ------------------------------------------------------------------
    # Skill helpers
    # ------------------------------------------------------------------

    async def _get_always_active_skills(
        self, agent: Any, conversation: Any
    ) -> List[str]:
        """Return skill names marked always-active (SKILL.md or catalog metadata)."""
        return await list_always_active_skill_names(self, agent, conversation)

    def _resolve_response_mode_from_result(
        self, result: Any, *, skill_catalog: Any = None
    ) -> str:
        activated = getattr(result, "activated_skills", None) or []
        if activated and skill_catalog is not None:
            try:
                return skill_catalog.get_response_mode_override(
                    set(activated), self.response_mode
                )
            except Exception as exc:
                logger.warning(
                    "AgentInteractAction: get_response_mode_override failed: %s", exc
                )
        return self.response_mode

    def _normalize_effective_response_mode(self, raw: str) -> str:
        if raw == "respond":
            return "respond"
        if raw == "publish":
            return "publish"
        fallback = (self.response_mode or "publish").strip().lower()
        return fallback if fallback in ("respond", "publish") else "publish"

    @staticmethod
    def _format_persona_directive(final_response: str) -> str:
        """Minimal Persona directive: sub-prompt only—utterance/history live in Persona scaffolding."""
        body = (final_response or "").strip()
        return f"Tell the user: {body}" if body else ""

    # ------------------------------------------------------------------
    # Hot-reload helpers (mirrors SkillInteractAction API)
    # ------------------------------------------------------------------

    @classmethod
    async def refresh_skills(cls, visitor: "InteractWalker") -> List[str]:
        """Re-discover skills and register any newly installed bundles."""
        return await _refresh_skills_impl(visitor)

    @classmethod
    async def remove_skill(cls, visitor: "InteractWalker", skill_name: str) -> bool:
        """Hot-unload a skill from the current session."""
        return await _remove_skill_impl(visitor, skill_name)

    async def healthcheck(self) -> bool:
        """Validate action configuration."""
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        agent = await self.get_agent()
        if not agent:
            return True
        persona = await self.get_action(PersonaAction)
        if not persona or not getattr(persona, "enabled", True):
            return False
        if not self._persona_description_text(persona):
            return False
        return True
