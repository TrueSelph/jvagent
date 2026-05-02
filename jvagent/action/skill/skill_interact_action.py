"""SkillInteractAction: interact-subsystem facade over SkillAction.

This action participates in the InteractWalker pipeline and adapts all
interact-specific constructs (InteractWalker, response_bus, interaction,
session_id, PersonaAction) into a ``SkillRunContext``, then delegates the
full agentic loop to ``SkillAction.run_to_completion()``.

All loop logic, tool management, skill discovery, checkpointing, evidence
logging, task tracking, and grounding verification now live in
``SkillAction``.  This module is intentionally thin.

Direct programmatic usage (non-interact)
----------------------------------------
Other Actions that want to run a reasoning-based, long-running skill task
without going through the interact subsystem should instantiate
``SkillAction`` directly::

    from jvagent.action.skill.skill_action import SkillAction
    from jvagent.action.skill.skill_action_contracts import SkillRunContext, SkillRunConfig

    ctx = SkillRunContext(
        utterance=my_task,
        conversation=conversation,
        model_action=await self.get_model_action(required=True),
        task_service=TaskService(conversation),
        config=SkillRunConfig(skills="-all"),
    )
    result = await SkillAction().run_to_completion(ctx)
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.skill.action_resolver import ActionResolver
from jvagent.action.skill.skill_action import SkillAction, _AgentShim
from jvagent.action.skill.skill_action_contracts import (
    DEFAULT_SKILL_MODEL,
    SkillRunConfig,
    SkillRunContext,
)
from jvagent.action.skill.skill_catalog import SkillCatalog

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


class SkillInteractAction(InteractAction):
    """Interact-subsystem adapter that delegates to ``SkillAction``.

    When activated by the InteractRouter, this action:
    1. Translates the ``InteractWalker`` into a ``SkillRunContext``.
    2. Calls ``SkillAction.run_to_completion(ctx)``.
    3. Delivers the result via the normal interact publish/respond pipeline.

    All loop parameters are still declared as ``attribute()`` fields so they
    can be configured from YAML/graph as before.

    Attributes:
        max_iterations: Hard cap on think-act-observe cycles.
        max_duration_seconds: Wall-clock timeout for the agentic loop.
        reasoning_budget_tokens: Generic reasoning budget tokens.
        model_action_type: LanguageModelAction entity type.
        model: Model identifier.
        model_temperature: Temperature for LLM generation.
        model_max_tokens: Max tokens for LLM generation.
        tool_servers: Names of MCPAction instances providing tools.
        allow_local_tools: Whether ToolExecutor can register local Python tools.
        stream_thinking: Stream extended thinking content as adhoc.
        stream_reasoning: Stream reasoning deltas for non-Anthropic providers.
        reasoning_extra: Optional provider-specific config escape hatch.
        stream_tool_progress: Stream tool call status as adhoc.
        max_full_tool_results: Keep last N tool results in full; summarize older.
        local_tools_path: Optional absolute path to a directory of tool modules.
    """

    weight: int = attribute(
        default=-60,
        description="Execution weight (after InteractRouter, before Persona)",
    )
    description: str = attribute(
        default="Long-running agentic loop for multi-step tasks with tool use.",
        description="Action description",
    )
    max_iterations: int = attribute(
        default=25, description="Hard cap on think-act-observe cycles"
    )
    max_duration_seconds: float = attribute(
        default=300.0, description="Wall-clock timeout for the agentic loop (seconds)"
    )
    reasoning_budget_tokens: int = attribute(
        default=0,
        description="Generic reasoning budget_tokens (0 disables budgeted reasoning).",
    )
    model_action_type: str = attribute(
        default="AnthropicLanguageModelAction",
        description="LanguageModelAction entity type",
    )
    model: str = attribute(default=DEFAULT_SKILL_MODEL, description="Model identifier")
    model_temperature: float = attribute(
        default=0.3, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=8192, description="Max tokens for LLM generation"
    )
    skills: Any = attribute(
        default=None,
        description="Skill selector: '-all' | list of names/globs | None",
    )
    denied_skills: List[str] = attribute(
        default_factory=list, description="Names/globs to exclude from skill bundles"
    )
    skills_source: str = attribute(
        default="both", description="Skill source: 'builtin' | 'app' | 'both' | 'none'"
    )
    tool_servers: List[str] = attribute(
        default_factory=list, description="Names of MCPAction instances providing tools"
    )
    allow_local_tools: bool = attribute(
        default=False,
        description="Whether ToolExecutor can register local Python tools",
    )
    stream_thinking: bool = attribute(
        default=True, description="Stream extended thinking content as adhoc"
    )
    stream_reasoning: bool = attribute(
        default=True,
        description="Stream reasoning deltas from non-Anthropic providers as thought messages.",
    )
    mirror_assistant_stream_as_thoughts: Optional[bool] = attribute(
        default=None,
        description="Provider-agnostic toggle to mirror assistant stream into thought stream.",
    )
    reasoning_enabled: Optional[bool] = attribute(
        default=None, description="Generic reasoning enable flag."
    )
    reasoning_extra: Optional[Dict[str, Any]] = attribute(
        default=None, description="Optional provider-native reasoning override dict."
    )
    reasoning_effort: Optional[str] = attribute(
        default=None,
        description="Generic reasoning effort: 'minimal', 'low', 'medium', 'high'.",
    )
    # Backward-compatibility aliases
    # ---- Deprecated aliases (target removal: next minor release, ~v0.x+1) ----
    # These aliases are forwarded in _build_run_config and will be removed once
    # all callers have migrated to the canonical field names above.
    thinking_budget_tokens: int = attribute(
        default=0,
        description="[Deprecated since v0.x — remove target: next minor] Use reasoning_budget_tokens.",
    )
    reasoning: Optional[Dict[str, Any]] = attribute(
        default=None,
        description="[Deprecated since v0.x — remove target: next minor] Use reasoning_extra.",
    )
    mirror_openai_assistant_stream_to_thoughts: Optional[bool] = attribute(
        default=None,
        description=(
            "[Deprecated since v0.x — remove target: next minor] "
            "Use mirror_assistant_stream_as_thoughts."
        ),
    )
    stream_tool_progress: bool = attribute(
        default=True, description="Stream tool call status as adhoc"
    )
    commit_intermediate_messages: bool = attribute(
        default=True,
        description="Publish mid-loop assistant text as user-category messages.",
    )
    relay_thoughts_to_channels: bool = attribute(
        default=False,
        description="If True, thought messages may be relayed to channel adapters.",
    )
    max_full_tool_results: int = attribute(
        default=10, description="Keep last N tool results in full; summarize older"
    )
    max_tool_result_tokens: int = attribute(
        default=400,
        description="Max estimated tokens for an individual tool result message",
    )
    tool_result_truncation_chars: int = attribute(
        default=500,
        description="Max chars streamed for individual tool-result thought updates",
    )
    history_limit: int = attribute(
        default=5,
        description="How many prior interactions to include in initial context",
    )
    call_timeout_seconds: float = attribute(
        default=60.0, description="Timeout in seconds for each tool call"
    )
    response_mode: str = attribute(
        default="publish",
        description=(
            "How to deliver the final response: 'publish' (direct) or 'respond' (via PersonaAction)"
        ),
    )
    local_tools_path: Optional[str] = attribute(
        default=None,
        description="Optional absolute path to a folder containing local tool .py files",
    )
    strict_grounding: bool = attribute(
        default=True, description="If True, enforce grounding-focused prompting"
    )
    plan_first: bool = attribute(
        default=True,
        description="If True, require task_tracker create before substantive tools "
        "(MCP, skill, local) except for meta-utterance turns; skill hub/helpers stay allowed",
    )
    enable_skill_helper_tools: bool = attribute(
        default=True,
        description="If True, register list_skills and skill_search helper tools",
    )
    skill_index_inline_max_skills: int = attribute(
        default=5,
        description=(
            "When the number of loaded skills is greater than this, omit per-skill "
            "frontmatter lines from the system prompt and instruct use of skill_search "
            "/ list_skills (requires enable_skill_helper_tools; otherwise the full index "
            "is still embedded)."
        ),
    )
    max_skill_activations: int = attribute(
        default=8,
        description="Maximum number of skill activations allowed within one loop",
    )
    max_iterations_per_skill: int = attribute(
        default=0,
        description="Maximum iterations allowed per skill activation (0 = unlimited)",
    )
    max_duration_per_skill_seconds: float = attribute(
        default=0.0,
        description="Maximum wall-clock seconds allowed per skill activation (0 = unlimited)",
    )
    stuck_detection_window: int = attribute(
        default=3, description="Consecutive identical signatures before stuck warning"
    )
    max_midcourse_corrections: int = attribute(
        default=2,
        description="Maximum stuck-detection reminders before forced termination",
    )
    progress_check_interval: int = attribute(
        default=5,
        description="Iterations between progress self-assessment prompts (0=disabled)",
    )
    final_review: bool = attribute(
        default=True,
        description="If True, run a final grounding review pass before publishing",
    )
    final_review_max_plan_steps: Optional[int] = attribute(
        default=None,
        description=(
            "If set, skip final review when the active task plan has this many "
            "steps or fewer."
        ),
    )
    semantic_skill_search: bool = attribute(
        default=False,
        description="If True, use LLM re-ranker for skill_search and plan_skills instead of lexical matching",
    )
    prioritize_skills_first: bool = attribute(
        default=True,
        description="If True, enforce skill-first retry before accepting a no-tool final answer",
    )
    skill_first_retry_limit: int = attribute(
        default=1, description="Maximum number of skill-first retry nudges in a loop."
    )
    task_nudge_retry_limit: int = attribute(
        default=2,
        description="Maximum retries when a task plan still has pending steps.",
    )
    skill_first_retry_min_relevance: float = attribute(
        default=0.25,
        description="Minimum skill relevance score before issuing skill-first nudge.",
    )
    conversational_skip_patterns: List[str] = attribute(
        default_factory=list,
        description="Optional regex strings for conversational skip.",
    )
    skill_first_conversational_heuristic: bool = attribute(
        default=True,
        description="Skip skill-first nudge for short low-relevance utterances.",
    )
    conversational_short_utterance_max_chars: int = attribute(
        default=60,
        description="Max utterance length (characters) for conversational heuristic.",
    )
    conversational_short_utterance_max_tokens: int = attribute(
        default=8, description="Max token count for conversational heuristic."
    )
    conversational_heuristic_max_relevance: float = attribute(
        default=3.0, description="Skip nudge only if top_relevance_score is below this."
    )
    conversational_min_response_chars: int = attribute(
        default=20,
        description="Minimum candidate length before conversational skip applies.",
    )
    meta_intent_skip_nudge: bool = attribute(
        default=True, description="Skip skill-first nudge for meta/identity questions."
    )
    meta_intent_patterns: List[str] = attribute(
        default_factory=list,
        description="Extra regex patterns for meta intent detection.",
    )
    degenerate_response_max_chars: int = attribute(
        default=25,
        description="Candidates shorter than this are treated as degenerate acks.",
    )
    best_candidate_shrink_ratio: float = attribute(
        default=0.4,
        description="If a later candidate is shorter than this ratio of best, prefer best.",
    )
    enable_checkpoints: bool = attribute(
        default=True,
        description="Persist loop iteration snapshots to conversation context for recovery.",
    )
    enable_evidence_log: bool = attribute(
        default=True,
        description="Record raw tool results in an evidence log for grounding verification.",
    )
    stuck_intent_jaccard_threshold: float = attribute(
        default=0.7,
        description=(
            "Jaccard similarity threshold (0-1) for semantic intent matching in stuck detection. "
            "Lower values flag more repetitions; higher values are more permissive."
        ),
    )
    max_total_task_nudges: int = attribute(
        default=6,
        description="Hard ceiling on total task-plan nudges across the entire loop.",
    )
    max_task_plan_steps: int = attribute(
        default=50,
        description="Maximum number of steps allowed in a single task plan.",
    )

    # -----------------------------------------------------------------------
    # InteractAction entry point
    # -----------------------------------------------------------------------

    async def execute(self, visitor: "InteractWalker") -> None:
        """Adapt InteractWalker to SkillRunContext and delegate to SkillAction."""
        if not self._ensure_interaction(visitor):
            logger.warning("SkillInteractAction: No interaction available")
            await visitor.unrecord_action_execution()
            return

        interaction = visitor.interaction
        conversation = visitor.conversation
        if not conversation:
            logger.warning("SkillInteractAction: No conversation available")
            await visitor.unrecord_action_execution()
            return

        try:
            # Resolve model action and attach ActionResolver to visitor
            model_action = await self.get_model_action(required=True)
            agent = getattr(visitor, "_agent", None)
            action_resolver = ActionResolver(agent) if agent else None
            visitor.action_resolver = action_resolver

            # Build run config from declared attributes (with deprecated alias mapping)
            cfg = self._build_run_config()

            # Discover skills once for persona-in-prompt policy (mirrors SkillAction.prepare_run).
            inject_persona_identity = (self.response_mode or "publish") == "respond"
            try:
                _visitor_shim = _AgentShim(
                    agent,
                    action_resolver,
                    user_id=getattr(visitor, "user_id", None),
                    conversation=conversation,
                    interaction=interaction,
                    session_id=visitor.session_id,
                    response_bus=visitor.response_bus,
                    channel=getattr(visitor, "channel", None),
                )
                _prompt_catalog = await SkillCatalog.discover(
                    visitor=_visitor_shim,
                    skills_selector=cfg.skills,
                    skills_source=cfg.skills_source,
                    denied_skills=cfg.denied_skills or None,
                )
                inject_persona_identity = (
                    SkillCatalog.should_inject_persona_identity_for_skill_prompt(
                        _prompt_catalog.skills, self.response_mode
                    )
                )
            except Exception as exc:
                logger.warning(
                    "SkillInteractAction: skill discovery for persona prompt policy failed: %s",
                    exc,
                )

            # Resolve persona for system prompt enrichment (skip when publish-only policy)
            agent_name = "Agent"
            agent_description = "An intelligent skills-based agent."
            if inject_persona_identity and agent:
                actions_manager = await agent.get_actions_manager()
                if actions_manager:
                    enabled_actions = await actions_manager.get_actions(
                        enabled_only=True
                    )
                    for action in enabled_actions:
                        if action.get_class_name() == "PersonaAction":
                            agent_name = getattr(action, "persona_name", "Agent")
                            agent_description = getattr(
                                action,
                                "persona_description",
                                "An intelligent skills-based agent.",
                            )
                            break

            # Publish callback bridges SkillAction output to the interact bus
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
                else:
                    if content:
                        await self.publish(
                            visitor=visitor,
                            content=content,
                            streaming_complete=streaming_complete,
                        )

            # skill_state shim for hot-reload (populated by SkillAction after prepare_run)
            visitor._skill_state = {
                "discovered_skills": {},
                "skill_catalog": None,
                "tool_executor": None,
                "action": self,
            }

            ctx = SkillRunContext(
                utterance=visitor.utterance or "",
                conversation=conversation,
                interaction=interaction,
                response_bus=visitor.response_bus,
                session_id=visitor.session_id,
                channel=getattr(visitor, "channel", None),
                stream=getattr(visitor, "stream", False),
                agent=agent,
                user_id=getattr(visitor, "user_id", None) or None,
                model_action=model_action,
                task_service=visitor.tasks,
                config=cfg,
                agent_name=agent_name,
                agent_description=agent_description,
                publish_callback=_publish_cb,
                skill_state=visitor._skill_state,
            )

            engine = SkillAction()
            result = await engine.run_to_completion(ctx)

            # Mark interaction executed regardless of response content (1.5).
            interaction.set_to_executed()

            # Deliver final response (PersonaAction / directives only when mode is respond)
            if result.final_response:
                skill_catalog = (visitor._skill_state or {}).get("skill_catalog")
                effective_mode = self._normalize_effective_response_mode(
                    self._resolve_response_mode_from_result(
                        result, skill_catalog=skill_catalog
                    )
                )
                use_persona = (
                    effective_mode == "respond"
                    and not SkillAction._is_degenerate_response(
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
                            "SkillInteractAction: skipping Persona; degenerate response; "
                            "publishing directly"
                        )
                    await self.publish(
                        visitor,
                        content=result.final_response,
                        streaming_complete=True,
                    )

        except Exception as exc:
            logger.error(
                "SkillInteractAction: Error during agentic loop: %s", exc, exc_info=True
            )
            await visitor.unrecord_action_execution()

    # -----------------------------------------------------------------------
    # Hot-reload helpers (unchanged API for tool modules)
    # -----------------------------------------------------------------------

    @classmethod
    async def refresh_skills(cls, visitor: "InteractWalker") -> List[str]:
        """Re-discover skills and register any newly installed bundles."""
        state = getattr(visitor, "_skill_state", None)
        if state is None:
            logger.warning("refresh_skills: no _skill_state on visitor")
            return []

        discovered_skills: Dict[str, Any] = state.get("discovered_skills") or {}
        skill_catalog = state.get("skill_catalog")
        tool_executor = state.get("tool_executor")
        action = state.get("action")

        await SkillCatalog.invalidate_cache(
            namespace=visitor._agent.namespace,
            agent_name=visitor._agent.name,
        )
        new_catalog = await SkillCatalog.discover(
            visitor=visitor,
            skills_selector=action.skills if action else None,
            skills_source=action.skills_source if action else "both",
            denied_skills=getattr(action, "denied_skills", None) if action else None,
        )
        new_skills = new_catalog.skills
        newly_found = [name for name in new_skills if name not in discovered_skills]

        if not newly_found and new_catalog.skills.keys() == discovered_skills.keys():
            return []

        if tool_executor:
            for skill_name in newly_found:
                skill_data = new_skills[skill_name]
                tool_executor.register_skill_bundle(
                    skill_name=skill_name,
                    dir_path=skill_data["dir"],
                    tool_files=skill_data.get("tool_files", []),
                    allowed_tools=skill_data.get("allowed_tools", []),
                )

        discovered_skills.update(new_skills)
        if skill_catalog is not None:
            skill_catalog.skills = discovered_skills

        logger.info(
            "refresh_skills: registered %d new skill(s): %s",
            len(newly_found),
            newly_found,
        )
        return newly_found

    @classmethod
    async def remove_skill(cls, visitor: "InteractWalker", skill_name: str) -> bool:
        """Hot-unload a skill from the current session."""
        state = getattr(visitor, "_skill_state", None)
        if state is None:
            return False
        discovered_skills = state.get("discovered_skills") or {}
        skill_catalog = state.get("skill_catalog")
        tool_executor = state.get("tool_executor")

        if skill_name not in discovered_skills:
            return False

        if tool_executor:
            tool_executor.unregister_skill_bundle(skill_name)

        discovered_skills.pop(skill_name, None)
        if skill_catalog and isinstance(getattr(skill_catalog, "skills", None), dict):
            skill_catalog.skills.pop(skill_name, None)

        await SkillCatalog.invalidate_cache(
            namespace=visitor._agent.namespace,
            agent_name=visitor._agent.name,
        )
        logger.info("remove_skill: removed skill '%s' from session", skill_name)
        return True

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _build_run_config(self) -> SkillRunConfig:
        """Map declared attributes → SkillRunConfig, handling deprecated aliases."""
        budget = int(getattr(self, "reasoning_budget_tokens", 0) or 0)
        legacy_budget = int(getattr(self, "thinking_budget_tokens", 0) or 0)
        if budget <= 0 and legacy_budget > 0:
            logger.warning(
                "SkillInteractAction: `thinking_budget_tokens` is deprecated; "
                "use `reasoning_budget_tokens`."
            )
            budget = legacy_budget

        reasoning_extra = getattr(self, "reasoning_extra", None)
        if reasoning_extra is None and getattr(self, "reasoning", None) is not None:
            logger.warning(
                "SkillInteractAction: `reasoning` is deprecated; use `reasoning_extra`."
            )
            reasoning_extra = getattr(self, "reasoning", None)

        mirror = getattr(self, "mirror_assistant_stream_as_thoughts", None)
        if (
            mirror is None
            and getattr(self, "mirror_openai_assistant_stream_to_thoughts", None)
            is not None
        ):
            logger.warning(
                "SkillInteractAction: `mirror_openai_assistant_stream_to_thoughts` is "
                "deprecated; use `mirror_assistant_stream_as_thoughts`."
            )
            mirror = getattr(self, "mirror_openai_assistant_stream_to_thoughts", None)

        return SkillRunConfig(
            model=self.model,
            model_temperature=self.model_temperature,
            model_max_tokens=self.model_max_tokens,
            model_action_type=self.model_action_type,
            max_iterations=self.max_iterations,
            max_duration_seconds=self.max_duration_seconds,
            reasoning_budget_tokens=budget,
            reasoning_enabled=getattr(self, "reasoning_enabled", None),
            reasoning_effort=getattr(self, "reasoning_effort", None),
            reasoning_extra=(
                reasoning_extra if isinstance(reasoning_extra, dict) else None
            ),
            mirror_assistant_stream_as_thoughts=mirror,
            stream_thinking=self.stream_thinking,
            stream_reasoning=self.stream_reasoning,
            stream_tool_progress=self.stream_tool_progress,
            commit_intermediate_messages=self.commit_intermediate_messages,
            relay_thoughts_to_channels=self.relay_thoughts_to_channels,
            max_full_tool_results=self.max_full_tool_results,
            max_tool_result_tokens=self.max_tool_result_tokens,
            tool_result_truncation_chars=self.tool_result_truncation_chars,
            history_limit=self.history_limit,
            call_timeout_seconds=self.call_timeout_seconds,
            skills=self.skills,
            denied_skills=list(getattr(self, "denied_skills", None) or []),
            skills_source=self.skills_source,
            enable_skill_helper_tools=self.enable_skill_helper_tools,
            skill_index_inline_max_skills=self.skill_index_inline_max_skills,
            max_skill_activations=self.max_skill_activations,
            max_iterations_per_skill=self.max_iterations_per_skill,
            max_duration_per_skill_seconds=self.max_duration_per_skill_seconds,
            semantic_skill_search=self.semantic_skill_search,
            skill_first_retry_limit=self.skill_first_retry_limit,
            skill_first_retry_min_relevance=self.skill_first_retry_min_relevance,
            prioritize_skills_first=self.prioritize_skills_first,
            tool_servers=list(self.tool_servers or []),
            allow_local_tools=self.allow_local_tools,
            local_tools_path=self.local_tools_path,
            strict_grounding=self.strict_grounding,
            plan_first=self.plan_first,
            final_review=self.final_review,
            final_review_max_plan_steps=self.final_review_max_plan_steps,
            task_nudge_retry_limit=self.task_nudge_retry_limit,
            max_total_task_nudges=self.max_total_task_nudges,
            max_task_plan_steps=self.max_task_plan_steps,
            stuck_detection_window=self.stuck_detection_window,
            stuck_intent_jaccard_threshold=self.stuck_intent_jaccard_threshold,
            max_midcourse_corrections=self.max_midcourse_corrections,
            progress_check_interval=self.progress_check_interval,
            enable_checkpoints=self.enable_checkpoints,
            enable_evidence_log=self.enable_evidence_log,
            conversational_skip_patterns=list(self.conversational_skip_patterns or []),
            skill_first_conversational_heuristic=self.skill_first_conversational_heuristic,
            conversational_short_utterance_max_chars=self.conversational_short_utterance_max_chars,
            conversational_short_utterance_max_tokens=self.conversational_short_utterance_max_tokens,
            conversational_heuristic_max_relevance=self.conversational_heuristic_max_relevance,
            conversational_min_response_chars=self.conversational_min_response_chars,
            meta_intent_skip_nudge=self.meta_intent_skip_nudge,
            meta_intent_patterns=list(self.meta_intent_patterns or []),
            degenerate_response_max_chars=self.degenerate_response_max_chars,
            best_candidate_shrink_ratio=self.best_candidate_shrink_ratio,
            response_mode=self.response_mode,
        )

    def _resolve_response_mode_from_result(
        self, result: Any, *, skill_catalog: Any = None
    ) -> str:
        """Effective response mode honoring per-skill ``response-mode`` in SKILL.md.

        Explicit ``respond`` or ``publish`` on activated skills overrides the
        action default; omitted / inherit uses :attr:`response_mode`.
        """
        activated = getattr(result, "activated_skills", None) or []
        if activated and skill_catalog is not None:
            try:
                return skill_catalog.get_response_mode_override(
                    set(activated), self.response_mode
                )
            except Exception as exc:
                logger.warning(
                    "SkillInteractAction: get_response_mode_override failed: %s", exc
                )
        return self.response_mode

    def _normalize_effective_response_mode(self, raw: str) -> str:
        """Coerce resolved mode to ``publish`` or ``respond`` for delivery logic."""
        if raw == "respond":
            return "respond"
        if raw == "publish":
            return "publish"
        fallback = (self.response_mode or "publish").strip().lower()
        if fallback in ("respond", "publish"):
            return fallback
        return "publish"

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

    async def healthcheck(self) -> bool:
        """Validate action configuration."""
        if not self.model_action_type:
            return False
        if self.max_iterations < 1:
            return False
        return True
