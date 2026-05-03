"""Map ``AgentInteractAction`` attributes → ``SkillRunConfig``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jvagent.action.agent_interact.skill_handler.contracts import SkillRunConfig

if TYPE_CHECKING:
    from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction

logger = logging.getLogger(__name__)


def build_skill_run_config(action: "AgentInteractAction") -> SkillRunConfig:
    """Build ``SkillRunConfig`` from declarative attributes on the interact action."""
    budget = int(getattr(action, "reasoning_budget_tokens", 0) or 0)
    legacy_budget = int(getattr(action, "thinking_budget_tokens", 0) or 0)
    if budget <= 0 and legacy_budget > 0:
        logger.warning(
            "AgentInteractAction: `thinking_budget_tokens` is deprecated; "
            "use `reasoning_budget_tokens`."
        )
        budget = legacy_budget

    reasoning_extra = getattr(action, "reasoning_extra", None)
    if reasoning_extra is None and getattr(action, "reasoning", None) is not None:
        logger.warning(
            "AgentInteractAction: `reasoning` is deprecated; use `reasoning_extra`."
        )
        reasoning_extra = getattr(action, "reasoning", None)

    mirror = getattr(action, "mirror_assistant_stream_as_thoughts", None)
    if (
        mirror is None
        and getattr(action, "mirror_openai_assistant_stream_to_thoughts", None)
        is not None
    ):
        logger.warning(
            "AgentInteractAction: `mirror_openai_assistant_stream_to_thoughts` is "
            "deprecated; use `mirror_assistant_stream_as_thoughts`."
        )
        mirror = getattr(action, "mirror_openai_assistant_stream_to_thoughts", None)

    return SkillRunConfig(
        model=action.model,
        model_temperature=action.model_temperature,
        model_max_tokens=action.model_max_tokens,
        model_action_type=action.model_action_type,
        max_iterations=action.max_iterations,
        max_duration_seconds=action.max_duration_seconds,
        reasoning_budget_tokens=budget,
        reasoning_enabled=getattr(action, "reasoning_enabled", None),
        reasoning_effort=getattr(action, "reasoning_effort", None),
        reasoning_extra=reasoning_extra if isinstance(reasoning_extra, dict) else None,
        mirror_assistant_stream_as_thoughts=mirror,
        stream_thinking=action.stream_thinking,
        stream_reasoning=action.stream_reasoning,
        stream_tool_progress=action.stream_tool_progress,
        commit_intermediate_messages=action.commit_intermediate_messages,
        relay_thoughts_to_channels=action.relay_thoughts_to_channels,
        max_full_tool_results=action.max_full_tool_results,
        max_tool_result_tokens=action.max_tool_result_tokens,
        tool_result_truncation_chars=action.tool_result_truncation_chars,
        history_limit=action.history_limit,
        call_timeout_seconds=action.call_timeout_seconds,
        skills=action.skills,
        denied_skills=list(getattr(action, "denied_skills", None) or []),
        skills_source=action.skills_source,
        enable_skill_helper_tools=action.enable_skill_helper_tools,
        skill_index_inline_max_skills=action.skill_index_inline_max_skills,
        max_skill_activations=action.max_skill_activations,
        max_iterations_per_skill=action.max_iterations_per_skill,
        max_duration_per_skill_seconds=action.max_duration_per_skill_seconds,
        semantic_skill_search=action.semantic_skill_search,
        skill_first_retry_limit=action.skill_first_retry_limit,
        skill_first_retry_min_relevance=action.skill_first_retry_min_relevance,
        prioritize_skills_first=action.prioritize_skills_first,
        tool_servers=list(action.tool_servers or []),
        allow_local_tools=action.allow_local_tools,
        local_tools_path=action.local_tools_path,
        strict_grounding=action.strict_grounding,
        plan_first=action.plan_first,
        final_review=action.final_review,
        final_review_max_plan_steps=action.final_review_max_plan_steps,
        task_nudge_retry_limit=action.task_nudge_retry_limit,
        max_total_task_nudges=action.max_total_task_nudges,
        max_task_plan_steps=action.max_task_plan_steps,
        stuck_detection_window=action.stuck_detection_window,
        stuck_intent_jaccard_threshold=action.stuck_intent_jaccard_threshold,
        max_midcourse_corrections=action.max_midcourse_corrections,
        progress_check_interval=action.progress_check_interval,
        enable_checkpoints=action.enable_checkpoints,
        enable_evidence_log=action.enable_evidence_log,
        conversational_skip_patterns=list(action.conversational_skip_patterns or []),
        skill_first_conversational_heuristic=action.skill_first_conversational_heuristic,
        conversational_short_utterance_max_chars=action.conversational_short_utterance_max_chars,
        conversational_short_utterance_max_tokens=action.conversational_short_utterance_max_tokens,
        conversational_heuristic_max_relevance=action.conversational_heuristic_max_relevance,
        conversational_min_response_chars=action.conversational_min_response_chars,
        meta_intent_skip_nudge=action.meta_intent_skip_nudge,
        meta_intent_patterns=list(action.meta_intent_patterns or []),
        degenerate_response_max_chars=action.degenerate_response_max_chars,
        best_candidate_shrink_ratio=action.best_candidate_shrink_ratio,
        response_mode=action.response_mode,
    )
