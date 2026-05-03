"""AgentInteractAction: unified skill-routing interact action.

Fuses the InteractRouter and SkillInteractAction into a single InteractAction
with a single walker visit.  Architecture:

Phase 1 — Route (SkillRouter):
    ``router_model`` + optional ``router_model_action_type`` (falls back to
    ``model_action_type``). Register one LM action per provider (e.g. OpenAI + Ollama).

Phase 2 — Execute:
    2a: Native conversation — ``native_conv_model_action_type`` (then router, then skill).
    2b: Skill loop — ``model_action_type`` (agentic think-act-observe).

Legacy InteractRouter + SkillInteractAction remain available for backward
compatibility; this class takes precedence when declared in agent.yaml.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from jvspatial.core.annotations import attribute

from jvagent.action.agent_interact.converse import NativeConversation
from jvagent.action.agent_interact.skill_handler.agentic_loop import (
    AgentInteractSkillAction,
    run_agentic_skill_loop,
)
from jvagent.action.agent_interact.skill_handler.always_active import (
    list_always_active_skill_names,
)
from jvagent.action.agent_interact.skill_handler.contracts import (
    DEFAULT_SKILL_MODEL,
    SkillRunContext,
)
from jvagent.action.agent_interact.skill_handler.hot_reload import (
    refresh_skills as _refresh_skills_impl,
)
from jvagent.action.agent_interact.skill_handler.hot_reload import (
    remove_skill as _remove_skill_impl,
)
from jvagent.action.agent_interact.skill_handler.run_config import (
    build_skill_run_config,
)
from jvagent.action.agent_interact.skill_handler.shim import AgentInteractVisitorShim
from jvagent.action.agent_interact.skill_handler.skill_router import SkillRouter
from jvagent.action.interact.base import InteractAction
from jvagent.action.router.routing_result import POSTURE_RESPOND, RoutingResult
from jvagent.action.skill.skill_catalog import SkillCatalog

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class AgentInteractAction(InteractAction):
    """Unified skill-routing interact action — single walker visit.

    Replaces the legacy InteractRouter + SkillInteractAction pair with a
    single action that routes posture/intent, handles conversational banter
    with a native fast model, and executes skill-based tasks in an agentic
    think-act-observe loop with mid-loop skill discovery.
    """

    weight: int = attribute(
        default=-200,
        description="Execution weight (replaces both InteractRouter at -200 and SkillInteractAction at -60)",
    )
    description: str = attribute(
        default="Unified skill-routing action: route posture/intent, handle conversation natively, execute skills in an agentic loop.",
        description="Action description",
    )

    # ── Router configuration (from InteractRouter) ──
    router_model: str = attribute(
        default="gpt-4o-mini", description="Model for routing LLM calls"
    )
    router_model_temperature: float = attribute(
        default=0.1, description="Temperature for routing LLM"
    )
    router_model_max_tokens: int = attribute(
        default=400, description="Max tokens for routing LLM"
    )
    enable_canned_response: bool = attribute(
        default=True, description="Publish canned response before execution"
    )
    canned_response_max_words: int = attribute(
        default=8, description="Max words for canned response"
    )
    skip_canned_for_intents: List[str] = attribute(
        default_factory=lambda: ["CONVERSATIONAL", "UNCLEAR", "INTERACTIVE"],
        description="Intent types that skip canned response",
    )
    confidence_threshold: float = attribute(
        default=0.7, description="Minimum confidence to proceed"
    )
    enable_clarification: bool = attribute(
        default=False, description="Request clarification on low confidence"
    )
    history_limit: int = attribute(
        default=3, description="Prior interactions in router context"
    )
    enable_accumulation: bool = attribute(
        default=True, description="Enable DEFER posture + fragment buffer"
    )
    max_fragment_buffer: int = attribute(
        default=5, description="Max deferred fragments"
    )
    enable_routing_cache: bool = attribute(
        default=False, description="Cache routing decisions"
    )
    exceptions: List[str] = attribute(
        default_factory=list, description="Actions that always execute"
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

    # ── Native conversational skill ──
    native_conv_enabled: bool = attribute(
        default=True, description="Enable native conversational path"
    )
    native_conv_model: str = attribute(
        default="gpt-4o-mini",
        description=(
            "Model id for native conversation; OpenAI default gpt-4o-mini is mapped "
            "to the primary LM action's model when that LM is Ollama"
        ),
    )
    native_conv_temperature: float = attribute(
        default=0.7, description="Temperature for native conversation"
    )
    native_conv_max_tokens: int = attribute(
        default=256, description="Max tokens for native conversation"
    )
    native_conv_context_limit: int = attribute(
        default=2, description="Prior interactions in conv context"
    )
    native_conv_persona_prompt: str = attribute(
        default=(
            "You are a friendly, concise assistant. "
            "Respond to greetings and casual conversation naturally. "
            "Keep responses brief (1-3 sentences). "
            "For task-oriented requests, acknowledge and hand off gracefully. "
            "Never mention that you are a 'native conversational skill' or "
            "reference internal system mechanics."
        ),
        description="System prompt for native conversational responses",
    )

    # ── Per-phase language model actions (class names, e.g. OpenAILanguageModelAction) ──
    model_action_type: str = attribute(default="AnthropicLanguageModelAction")
    router_model_action_type: str = attribute(
        default="",
        description=(
            "LanguageModelAction class for Phase 1 (SkillRouter). "
            "Empty: use model_action_type."
        ),
    )
    native_conv_model_action_type: str = attribute(
        default="",
        description=(
            "LanguageModelAction class for native conversational replies. "
            "Empty: use router_model_action_type, then model_action_type."
        ),
    )
    # ── Skill loop (all SkillInteractAction attributes) ──
    model: str = attribute(default=DEFAULT_SKILL_MODEL)
    model_temperature: float = attribute(default=0.3)
    model_max_tokens: int = attribute(default=8192)
    max_iterations: int = attribute(default=25)
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
    skills: Any = attribute(default=None)
    denied_skills: List[str] = attribute(default_factory=list)
    skills_source: str = attribute(default="both")
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
    strict_grounding: bool = attribute(default=True)
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
    conversational_skip_patterns: List[str] = attribute(default_factory=list)
    skill_first_conversational_heuristic: bool = attribute(default=True)
    conversational_short_utterance_max_chars: int = attribute(default=60)
    conversational_short_utterance_max_tokens: int = attribute(default=8)
    conversational_heuristic_max_relevance: float = attribute(default=3.0)
    conversational_min_response_chars: int = attribute(default=20)
    meta_intent_skip_nudge: bool = attribute(default=True)
    meta_intent_patterns: List[str] = attribute(default_factory=list)
    degenerate_response_max_chars: int = attribute(default=25)
    best_candidate_shrink_ratio: float = attribute(default=0.4)
    response_mode: str = attribute(default="publish")

    # ── Deprecated aliases (forwarded in _build_run_config) ──
    thinking_budget_tokens: int = attribute(default=0)
    reasoning: Optional[Dict[str, Any]] = attribute(default=None)
    mirror_openai_assistant_stream_to_thoughts: Optional[bool] = attribute(default=None)

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
        native = self._strip_model_action_type(
            getattr(self, "native_conv_model_action_type", None)
        )
        if purpose == "skill":
            return skill
        if purpose == "router":
            return router or skill
        if purpose == "native":
            return native or router or skill
        return skill

    async def get_model_action(
        self,
        required: bool = False,
        *,
        purpose: str = "skill",
    ) -> Optional[Any]:
        """Return the LanguageModelAction for *purpose* (router / native / skill).

        Each *purpose* maps to a registered LM action class name:
        ``router_model_action_type`` → ``native_conv_model_action_type`` →
        ``model_action_type`` with the fallbacks described on those attributes.

        Args:
            required: If True, raise when no LM action can be resolved.
            purpose: ``skill`` (agentic loop), ``router`` (SkillRouter), or
                ``native`` (NativeConversation).
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
        router = SkillRouter(self)
        posture, routing = await router.route(visitor)

        if posture == "SUPPRESS" or posture == "DEFER":
            return  # walk path already cleared by router

        if routing is None:
            routing = RoutingResult(posture=POSTURE_RESPOND)

        # ── Phase 2: Execute ──
        if self.native_conv_enabled and (
            routing.intent_type == "CONVERSATIONAL" or not routing.actions
        ):
            await self._phase_execute_conversational(visitor)
        else:
            await self._phase_execute_skill_loop(visitor, routing)

    # ------------------------------------------------------------------
    # Phase 2a: Native conversational
    # ------------------------------------------------------------------

    async def _phase_execute_conversational(self, visitor: "InteractWalker") -> None:
        conv = NativeConversation(self)
        await conv.respond(visitor)

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

            # ── Resolve persona identity for system prompt ──
            agent_name = "Agent"
            agent_description = "An intelligent skills-based agent."

            inject_persona_identity = (self.response_mode or "publish") == "respond"
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
                prompt_catalog = await SkillCatalog.discover(
                    visitor=visitor_shim,
                    skills_selector=cfg.skills,
                    skills_source=cfg.skills_source,
                    denied_skills=cfg.denied_skills or None,
                )
                inject_persona_identity = (
                    SkillCatalog.should_inject_persona_identity_for_skill_prompt(
                        prompt_catalog.skills, self.response_mode
                    )
                )
            except Exception as exc:
                logger.warning(
                    "AgentInteractAction: skill discovery for persona prompt failed: %s",
                    exc,
                )

            if inject_persona_identity and agent:
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    enabled_actions = await actions_manager.get_actions(
                        enabled_only=True
                    )
                    for a in enabled_actions:
                        if a.get_class_name() == "PersonaAction":
                            agent_name = getattr(a, "persona_name", "Agent")
                            agent_description = getattr(
                                a,
                                "persona_description",
                                "An intelligent skills-based agent.",
                            )
                            break

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

            # Build SkillRunContext
            ctx = SkillRunContext(
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
                preloaded_skills=preloaded,
                publish_callback=_publish_cb,
                agent_name=agent_name,
                agent_description=agent_description,
                skill_state=visitor._skill_state,
            )

            result = await run_agentic_skill_loop(ctx)

            # Mark interaction as executed
            interaction.set_to_executed()

            # Deliver final response
            if result.final_response:
                skill_catalog = (visitor._skill_state or {}).get("skill_catalog")
                effective_mode = self._normalize_effective_response_mode(
                    self._resolve_response_mode_from_result(
                        result, skill_catalog=skill_catalog
                    )
                )
                use_persona = (
                    effective_mode == "respond"
                    and not AgentInteractSkillAction._is_degenerate_response(
                        result.final_response,
                        max_chars=cfg.degenerate_response_max_chars,
                    )
                )
                if use_persona:
                    await visitor.add_directive(
                        self._format_persona_directive(
                            visitor.utterance, result.final_response
                        )
                    )
                    await self.respond(visitor)
                else:
                    if effective_mode == "respond":
                        logger.warning(
                            "AgentInteractAction: skipping Persona; degenerate response; "
                            "publishing directly"
                        )
                    await self.publish(
                        visitor,
                        content=result.final_response,
                        streaming_complete=True,
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
    def _format_persona_directive(utterance: Optional[str], final_response: str) -> str:
        uq = (utterance or "").strip() or "(no utterance)"
        return (
            f'A verified research result has been produced for the user\'s question: "{uq}"\n\n'
            f"VERIFIED CONTENT:\n{final_response}\n\n"
            "Rewrite as a natural, direct user reply.\n"
            "- Lead with the answer; no process narration.\n"
            "- Remove internal terms/tags (e.g. PageIndex, skill loop, retrieval, assimilate, assimilated, document index, [PageIndex], [General Knowledge]).\n"
            "- Preserve all facts, names, quotes, and URLs exactly; do not add or invent anything.\n"
            "- Keep citations human-friendly (title + link), and keep the tone knowledgeable and friendly."
        )

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
        return True
