"""Lifecycle tests for SkillInteractAction.execute delegation to SkillAction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_action_contracts import SkillRunResult
from jvagent.action.skill.skill_interact_action import SkillInteractAction


def _mock_action() -> MagicMock:
    action = MagicMock(spec=SkillInteractAction)
    action.get_class_name = MagicMock(return_value="SkillInteractAction")
    action._ensure_interaction = MagicMock(return_value=True)
    action.get_model_action = AsyncMock(return_value=MagicMock())
    action.publish = AsyncMock()
    action.publish_thought = AsyncMock()
    action.respond = AsyncMock()
    action.unrecord_action_execution = AsyncMock()
    action.local_tools_path = None
    action.tool_servers = []
    action.call_timeout_seconds = 30.0
    action.skills = []
    action.skills_source = "both"
    action.denied_skills = []
    action.stream_tool_progress = False
    action.stream_thinking = False
    action.commit_intermediate_messages = True
    action.relay_thoughts_to_channels = False
    action.strict_grounding = True
    action.plan_first = True
    action.final_review = False
    action.max_skill_activations = 5
    action.stuck_detection_window = 3
    action.max_midcourse_corrections = 2
    action.enable_skill_helper_tools = True
    action.response_mode = "publish"
    action.model = "claude-sonnet-4-20250514"
    action.model_temperature = 0.3
    action.model_max_tokens = 8192
    action.model_action_type = "AnthropicLanguageModelAction"
    action.reasoning_budget_tokens = 0
    action.thinking_budget_tokens = 0
    action.reasoning_enabled = None
    action.reasoning_extra = None
    action.reasoning_effort = None
    action.mirror_assistant_stream_as_thoughts = None
    action.max_full_tool_results = 10
    action.max_tool_result_tokens = 400
    action.tool_result_truncation_chars = 500
    action.history_limit = 5
    action.max_iterations = 10
    action.max_duration_seconds = 300.0
    action.task_nudge_retry_limit = 1
    action.skill_first_retry_limit = 1
    action.skill_first_retry_min_relevance = 0.25
    action.prioritize_skills_first = True
    action.meta_intent_skip_nudge = True
    action.meta_intent_patterns = []
    action.conversational_skip_patterns = []
    action.skill_first_conversational_heuristic = True
    action.conversational_short_utterance_max_chars = 60
    action.conversational_short_utterance_max_tokens = 8
    action.conversational_heuristic_max_relevance = 3.0
    action.conversational_min_response_chars = 20
    action.degenerate_response_max_chars = 25
    action.best_candidate_shrink_ratio = 0.4
    action.allow_local_tools = False
    action.plan_first = True
    action.final_review = False
    action.progress_check_interval = 3
    action.enable_checkpoints = True
    action.enable_evidence_log = True
    action.stuck_intent_similarity_threshold = 0.7
    action.max_total_task_nudges = 6
    action.max_task_plan_steps = 50
    action.stream_thinking = True
    action.stream_reasoning = True
    action.mirror_openai_assistant_stream_to_thoughts = None
    # Delegate _build_run_config to the real method via SkillInteractAction
    action._build_run_config = lambda: SkillInteractAction._build_run_config(action)
    action._resolve_response_mode_from_result = lambda r, **kwargs: "publish"
    action._format_persona_directive = lambda u, f: f
    return action


def _visitor_with_tasks() -> tuple:
    visitor = MagicMock()
    visitor.utterance = "hello"
    visitor.interaction = SimpleNamespace(
        utterance="hello",
        set_to_executed=MagicMock(),
    )
    visitor.conversation = MagicMock()
    visitor.unrecord_action_execution = AsyncMock()
    visitor.response_bus = MagicMock()
    visitor.session_id = "test-session"
    visitor.channel = None
    visitor.stream = False
    visitor.tasks = MagicMock()

    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=None)
    visitor._agent = agent
    return visitor


def _make_skill_result(response: str = "Done!") -> SkillRunResult:
    return SkillRunResult(
        final_response=response,
        termination_reason="completed",
        stuck_corrections=0,
        result_attributions=[],
        iterations=1,
        duration_seconds=0.1,
        task_id=None,
        activated_skills=[],
    )


@pytest.mark.asyncio
async def test_execute_completes_and_publishes():
    """execute() delegates to SkillAction.run_to_completion and publishes the result."""
    action = _mock_action()
    visitor = _visitor_with_tasks()

    with patch(
        "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
        new=AsyncMock(return_value=_make_skill_result("The answer is 42.")),
    ):
        await SkillInteractAction.execute(action, visitor)

    action.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_still_cleans_up_on_error():
    """execute() calls unrecord_action_execution when SkillAction raises."""
    action = _mock_action()
    visitor = _visitor_with_tasks()

    with patch(
        "jvagent.action.skill.skill_action.SkillAction.run_to_completion",
        new=AsyncMock(side_effect=RuntimeError("loop exploded")),
    ):
        await SkillInteractAction.execute(action, visitor)

    visitor.unrecord_action_execution.assert_awaited_once()
