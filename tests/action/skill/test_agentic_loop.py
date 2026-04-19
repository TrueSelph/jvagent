"""Tests for agentic loop lifecycle: skill-first retry, forced termination, stuck detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.skill_interact_action import SkillInteractAction
from jvagent.action.skill.stuck_detector import StuckDetector, StuckDetectorConfig

# --- Skill-first retry logic ---


class TestShouldRetryForSkillFirst:
    def _make_action(self, prioritize=True, retry_limit=1):
        action = MagicMock(spec=SkillInteractAction)
        action.prioritize_skills_first = prioritize
        action.skill_first_retry_limit = retry_limit
        action._should_retry_for_skill_first = (
            lambda ds, te, u, r: SkillInteractAction._should_retry_for_skill_first(
                action, ds, te, u, r
            )
        )
        return action

    def test_fires_when_skills_available_none_activated_under_limit(self):
        action = self._make_action()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            action._should_retry_for_skill_first({"gmail": {}}, tool_executor, "any", 0)
            is True
        )

    def test_does_not_fire_when_disabled(self):
        action = self._make_action(prioritize=False)
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            action._should_retry_for_skill_first({"gmail": {}}, tool_executor, "any", 0)
            is False
        )

    def test_does_not_fire_when_no_skills(self):
        action = self._make_action()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            action._should_retry_for_skill_first(None, tool_executor, "any", 0) is False
        )

    def test_does_not_fire_when_skills_activated(self):
        action = self._make_action()
        tool_executor = MagicMock()
        tool_executor.activated_skills = {"gmail"}
        assert (
            action._should_retry_for_skill_first({"gmail": {}}, tool_executor, "any", 0)
            is False
        )

    def test_does_not_fire_when_retry_limit_exceeded(self):
        action = self._make_action(retry_limit=1)
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            action._should_retry_for_skill_first({"gmail": {}}, tool_executor, "any", 1)
            is False
        )

    def test_does_not_fire_when_no_tool_executor(self):
        action = self._make_action()
        assert (
            action._should_retry_for_skill_first({"gmail": {}}, None, "any", 0) is False
        )


# --- StuckDetector integration within loop context ---


class TestStuckDetectorInLoop:
    def test_stuck_detector_created_with_config(self):
        config = StuckDetectorConfig(window_size=5, max_corrections=3)
        detector = StuckDetector(config)
        assert detector.corrections == 0

    def test_stuck_detector_records_and_detects(self):
        detector = StuckDetector(StuckDetectorConfig(window_size=2, max_corrections=1))
        tool_calls = [{"function": {"name": "search", "arguments": "{}"}}]
        result1 = detector.record(tool_calls)
        assert result1 is None
        result2 = detector.record(tool_calls)
        assert result2 is not None
        assert result2 != "FORCE_TERMINATE"

    def test_stuck_detector_force_terminates(self):
        detector = StuckDetector(StuckDetectorConfig(window_size=2, max_corrections=0))
        tool_calls = [{"function": {"name": "search", "arguments": "{}"}}]
        detector.record(tool_calls)
        result = detector.record(tool_calls)
        assert result == "FORCE_TERMINATE"


# --- SkillCatalog search integration ---


class TestCatalogSearchIntegration:
    def test_search_returns_formatted_matches(self):
        catalog = SkillCatalog(
            {
                "research": {
                    "description": "Investigate topics and synthesize findings.",
                    "metadata": {"tags": ["research", "analysis"]},
                    "tool_files": [],
                    "requires_actions": [],
                },
                "web_search": {
                    "description": "Search the public web.",
                    "metadata": {"tags": ["search"]},
                    "tool_files": [],
                    "requires_actions": [],
                },
            }
        )
        result = catalog.search("research and analysis", top_k=3)
        assert "research" in result

    def test_search_empty_catalog(self):
        catalog = SkillCatalog({})
        result = catalog.search("anything", top_k=3)
        assert "Available skills:" in result


# --- LoopContext format conversion integration ---


class TestAnthropicFormatConversion:
    def test_tool_result_blocks_merged(self):
        from jvagent.action.skill.loop_context import LoopContext

        messages = [
            {"role": "tool", "tool_call_id": "1", "content": "result1"},
            {"role": "tool", "tool_call_id": "2", "content": "result2"},
        ]
        converted = LoopContext.convert_for_provider(messages, "anthropic")
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert len(converted[0]["content"]) == 2
        assert converted[0]["content"][0]["type"] == "tool_result"

    def test_anthropic_tool_use_in_assistant(self):
        from jvagent.action.skill.loop_context import LoopContext

        messages = [
            {
                "role": "assistant",
                "content": "Checking",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "function": {"name": "search", "arguments": '{"q": "test"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "found"},
        ]
        converted = LoopContext.convert_for_provider(messages, "anthropic")
        assert converted[0]["role"] == "assistant"
        assert isinstance(converted[0]["content"], list)
        assert converted[0]["content"][1]["type"] == "tool_use"
        assert converted[1]["role"] == "user"
        assert converted[1]["content"][0]["type"] == "tool_result"
