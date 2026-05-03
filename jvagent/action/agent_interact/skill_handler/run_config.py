"""Map ``AgentInteractAction`` attributes → ``SkillRunConfig``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jvagent.action.agent_interact.skill_handler.contracts import SkillRunConfig

if TYPE_CHECKING:
    from jvagent.action.agent_interact.agent_interact_action import AgentInteractAction

logger = logging.getLogger(__name__)

_CONVERSATIONAL_SKIP_PATTERNS: list = []
_SKILL_FIRST_CONVERSATIONAL_HEURISTIC: bool = True
_CONVERSATIONAL_SHORT_UTTERANCE_MAX_CHARS: int = 60
_CONVERSATIONAL_SHORT_UTTERANCE_MAX_TOKENS: int = 8
_CONVERSATIONAL_HEURISTIC_MAX_RELEVANCE: float = 3.0
_CONVERSATIONAL_MIN_RESPONSE_CHARS: int = 20
_META_INTENT_SKIP_NUDGE: bool = True
_META_INTENT_PATTERNS: list = []
_DEGENERATE_RESPONSE_MAX_CHARS: int = 25
_BEST_CANDIDATE_SHRINK_RATIO: float = 0.4


def build_skill_run_config(action: "AgentInteractAction") -> SkillRunConfig:
    """Build ``SkillRunConfig`` from declarative attributes on the interact action."""
    return SkillRunConfig(
        model=action.model,
        model_temperature=getattr(action, "model_temperature", 0.3),
        model_max_tokens=getattr(action, "model_max_tokens", 8192),
        model_action_type=action.model_action_type,
        max_iterations=action.max_iterations,
        max_duration_seconds=getattr(action, "max_duration_seconds", 300.0),
        reasoning_budget_tokens=int(getattr(action, "reasoning_budget_tokens", 0) or 0),
        reasoning_enabled=getattr(action, "reasoning_enabled", None),
        reasoning_effort=getattr(action, "reasoning_effort", None),
        reasoning_extra=(
            getattr(action, "reasoning_extra", None)
            if isinstance(getattr(action, "reasoning_extra", None), dict)
            else None
        ),
        mirror_assistant_stream_as_thoughts=getattr(
            action, "mirror_assistant_stream_as_thoughts", None
        ),
        stream_thinking=getattr(action, "stream_thinking", True),
        stream_reasoning=getattr(action, "stream_reasoning", True),
        stream_tool_progress=getattr(action, "stream_tool_progress", True),
        commit_intermediate_messages=getattr(
            action, "commit_intermediate_messages", True
        ),
        relay_thoughts_to_channels=getattr(action, "relay_thoughts_to_channels", False),
        max_full_tool_results=getattr(action, "max_full_tool_results", 10),
        max_tool_result_tokens=getattr(action, "max_tool_result_tokens", 400),
        tool_result_truncation_chars=getattr(
            action, "tool_result_truncation_chars", 500
        ),
        history_limit=action.history_limit,
        call_timeout_seconds=getattr(action, "call_timeout_seconds", 60.0),
        skills=action.skills,
        denied_skills=list(getattr(action, "denied_skills", None) or []),
        skills_source=getattr(action, "skills_source", "both"),
        enable_skill_helper_tools=getattr(action, "enable_skill_helper_tools", True),
        skill_index_inline_max_skills=getattr(
            action, "skill_index_inline_max_skills", 5
        ),
        max_skill_activations=getattr(action, "max_skill_activations", 8),
        max_iterations_per_skill=getattr(action, "max_iterations_per_skill", 0),
        max_duration_per_skill_seconds=getattr(
            action, "max_duration_per_skill_seconds", 0.0
        ),
        semantic_skill_search=getattr(action, "semantic_skill_search", False),
        skill_first_retry_limit=getattr(action, "skill_first_retry_limit", 1),
        skill_first_retry_min_relevance=getattr(
            action, "skill_first_retry_min_relevance", 0.25
        ),
        prioritize_skills_first=getattr(action, "prioritize_skills_first", True),
        tool_servers=list(getattr(action, "tool_servers", None) or []),
        allow_local_tools=getattr(action, "allow_local_tools", False),
        local_tools_path=getattr(action, "local_tools_path", None),
        strict_grounding=action.strict_grounding,
        plan_first=getattr(action, "plan_first", True),
        final_review=getattr(action, "final_review", True),
        final_review_max_plan_steps=getattr(
            action, "final_review_max_plan_steps", None
        ),
        task_nudge_retry_limit=getattr(action, "task_nudge_retry_limit", 2),
        max_total_task_nudges=getattr(action, "max_total_task_nudges", 6),
        max_task_plan_steps=getattr(action, "max_task_plan_steps", 50),
        stuck_detection_window=getattr(action, "stuck_detection_window", 3),
        stuck_intent_jaccard_threshold=getattr(
            action, "stuck_intent_jaccard_threshold", 0.7
        ),
        max_midcourse_corrections=getattr(action, "max_midcourse_corrections", 2),
        progress_check_interval=getattr(action, "progress_check_interval", 5),
        enable_checkpoints=getattr(action, "enable_checkpoints", True),
        enable_evidence_log=getattr(action, "enable_evidence_log", True),
        conversational_skip_patterns=_CONVERSATIONAL_SKIP_PATTERNS,
        skill_first_conversational_heuristic=_SKILL_FIRST_CONVERSATIONAL_HEURISTIC,
        conversational_short_utterance_max_chars=_CONVERSATIONAL_SHORT_UTTERANCE_MAX_CHARS,
        conversational_short_utterance_max_tokens=_CONVERSATIONAL_SHORT_UTTERANCE_MAX_TOKENS,
        conversational_heuristic_max_relevance=_CONVERSATIONAL_HEURISTIC_MAX_RELEVANCE,
        conversational_min_response_chars=_CONVERSATIONAL_MIN_RESPONSE_CHARS,
        meta_intent_skip_nudge=_META_INTENT_SKIP_NUDGE,
        meta_intent_patterns=_META_INTENT_PATTERNS,
        degenerate_response_max_chars=_DEGENERATE_RESPONSE_MAX_CHARS,
        best_candidate_shrink_ratio=_BEST_CANDIDATE_SHRINK_RATIO,
        response_mode=action.response_mode,
    )
