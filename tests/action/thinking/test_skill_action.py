"""Tests for SkillAction: prompt composition, tool filtering, and validation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.model.language.tools import ToolDefinition
from jvagent.action.skill.skill_action import SkillAction


class TestSkillActionPromptComposition:
    """Test system prompt composition."""

    @pytest.mark.asyncio
    async def test_compose_system_prompt_with_variables(self):
        skill = _make_skill(
            system_prompt_template="You are a {role} assistant. {extra}",
            prompt_variables={"role": "code review", "extra": "Focus on bugs."},
        )
        result = skill.compose_system_prompt()
        assert "code review" in result
        assert "Focus on bugs." in result

    @pytest.mark.asyncio
    async def test_compose_system_prompt_with_override_variables(self):
        skill = _make_skill(
            system_prompt_template="You are a {role} assistant. {extra}",
            prompt_variables={"role": "general", "extra": "Default."},
        )
        result = skill.compose_system_prompt(variables={"extra": "Custom."})
        assert "Custom." in result
        assert "general" in result

    @pytest.mark.asyncio
    async def test_compose_system_prompt_missing_variable(self):
        skill = _make_skill(
            system_prompt_template="Hello {missing_var}",
            prompt_variables={},
        )
        # Should not raise, just return template
        result = skill.compose_system_prompt()
        assert "missing_var" in result or "{missing_var}" in result

    def test_compose_utterance_with_prepend_append(self):
        skill = _make_skill(
            prepend_to_utterance="[Context: reviewing code]",
            append_to_utterance="Please provide detailed feedback.",
        )
        result = skill.compose_utterance("Review this file")
        assert "[Context: reviewing code]" in result
        assert "Review this file" in result
        assert "Please provide detailed feedback." in result

    def test_compose_utterance_without_prepend_append(self):
        skill = _make_skill()
        result = skill.compose_utterance("Hello")
        assert result == "Hello"


class TestSkillActionToolFilter:
    """Test tool filtering by allowed/denied patterns."""

    def test_no_filters_returns_all(self):
        skill = _make_skill()
        tools = [
            _make_tool_def("read_file"),
            _make_tool_def("write_file"),
            _make_tool_def("search"),
        ]
        filtered = skill.get_tool_filter(tools)
        assert len(filtered) == 3

    def test_allowed_patterns_filters(self):
        skill = _make_skill(allowed_tool_patterns=["read_*"])
        tools = [
            _make_tool_def("read_file"),
            _make_tool_def("write_file"),
        ]
        filtered = skill.get_tool_filter(tools)
        assert len(filtered) == 1
        assert filtered[0].name == "read_file"

    def test_denied_patterns_filters(self):
        skill = _make_skill(denied_tool_patterns=["write_*", "delete_*"])
        tools = [
            _make_tool_def("read_file"),
            _make_tool_def("write_file"),
            _make_tool_def("delete_file"),
        ]
        filtered = skill.get_tool_filter(tools)
        assert len(filtered) == 1
        assert filtered[0].name == "read_file"

    def test_tool_description_overrides(self):
        skill = _make_skill(
            tool_overrides={"search": {"description": "Search code only"}}
        )
        tools = [_make_tool_def("search", description="Generic search")]
        filtered = skill.get_tool_filter(tools)
        assert filtered[0].description == "Search code only"


class TestSkillActionValidation:
    """Test required tools validation."""

    def test_validate_tools_all_present(self):
        skill = _make_skill(required_tools=["read_file", "search"])
        missing = skill.validate_tools_available({"read_file", "search", "write_file"})
        assert missing == []

    def test_validate_tools_missing(self):
        skill = _make_skill(required_tools=["read_file", "nonexistent"])
        missing = skill.validate_tools_available({"read_file"})
        assert missing == ["nonexistent"]

    def test_validate_tools_empty_required(self):
        skill = _make_skill(required_tools=[])
        missing = skill.validate_tools_available(set())
        assert missing == []


class TestSkillActionModelOverrides:
    """Test model parameter overrides."""

    def test_get_model_overrides_all_set(self):
        skill = _make_skill(
            model="claude-opus-4-20250514",
            model_temperature=0.1,
            model_max_tokens=16000,
            max_iterations=50,
            max_duration_seconds=600.0,
            thinking_budget_tokens=20000,
        )
        overrides = skill.get_model_overrides()
        assert overrides["model"] == "claude-opus-4-20250514"
        assert overrides["model_temperature"] == 0.1
        assert overrides["max_iterations"] == 50

    def test_get_model_overrides_none_omitted(self):
        skill = _make_skill()  # All defaults are None
        overrides = skill.get_model_overrides()
        assert overrides == {}


class TestSkillActionHealthcheck:
    """Test healthcheck validation."""

    @pytest.mark.asyncio
    async def test_healthcheck_valid(self):
        skill = _make_skill(skill_name="test", system_prompt_template="You are...")
        result = await skill.healthcheck()
        assert result is True

    @pytest.mark.asyncio
    async def test_healthcheck_missing_name(self):
        skill = _make_skill(skill_name="", system_prompt_template="You are...")
        result = await skill.healthcheck()
        assert result is False


# --- Helpers ---


def _make_skill(**kwargs):
    """Create a SkillAction-like object for testing without graph persistence."""
    skill = MagicMock(spec=SkillAction)
    # Set defaults
    skill.skill_name = kwargs.get("skill_name", "test_skill")
    skill.system_prompt_template = kwargs.get(
        "system_prompt_template", "You are an assistant."
    )
    skill.prompt_variables = kwargs.get("prompt_variables", {})
    skill.prepend_to_utterance = kwargs.get("prepend_to_utterance", "")
    skill.append_to_utterance = kwargs.get("append_to_utterance", "")
    skill.required_tools = kwargs.get("required_tools", [])
    skill.optional_tools = kwargs.get("optional_tools", [])
    skill.tool_overrides = kwargs.get("tool_overrides", {})
    skill.allowed_tool_patterns = kwargs.get("allowed_tool_patterns", [])
    skill.denied_tool_patterns = kwargs.get("denied_tool_patterns", [])
    skill.max_iterations = kwargs.get("max_iterations", None)
    skill.max_duration_seconds = kwargs.get("max_duration_seconds", None)
    skill.thinking_budget_tokens = kwargs.get("thinking_budget_tokens", None)
    skill.model = kwargs.get("model", None)
    skill.model_temperature = kwargs.get("model_temperature", None)
    skill.model_max_tokens = kwargs.get("model_max_tokens", None)

    # Wire up real methods
    skill.compose_system_prompt = (
        lambda variables=None: SkillAction.compose_system_prompt(skill, variables)
    )
    skill.compose_utterance = lambda raw: SkillAction.compose_utterance(skill, raw)
    skill.get_tool_filter = lambda tools: SkillAction.get_tool_filter(skill, tools)
    skill.validate_tools_available = lambda names: SkillAction.validate_tools_available(
        skill, names
    )
    skill.get_model_overrides = lambda: SkillAction.get_model_overrides(skill)

    async def _healthcheck():
        return await SkillAction.healthcheck(skill)

    skill.healthcheck = _healthcheck

    return skill


def _make_tool_def(name: str, description: str = "A tool") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
    )
