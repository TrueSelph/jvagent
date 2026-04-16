"""Tests for ThinkingInteractAction: agentic loop, model kwargs, and message truncation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.thinking.thinking_interact_action import ThinkingInteractAction


class TestThinkingInteractActionModelKwargs:
    """Test model keyword argument building."""

    def test_build_model_kwargs_defaults(self):
        action = _make_thinking_action()
        kwargs = action._build_model_kwargs()
        assert kwargs["model"] == "claude-sonnet-4-20250514"
        assert kwargs["temperature"] == 0.3
        assert "thinking" not in kwargs

    def test_build_model_kwargs_with_thinking(self):
        action = _make_thinking_action(thinking_budget_tokens=5000)
        kwargs = action._build_model_kwargs()
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 5000}
        # max_tokens should be >= budget_tokens + 1
        assert kwargs["max_tokens"] >= 5001

    def test_build_model_kwargs_skill_overrides(self):
        skill = MagicMock()
        skill.get_model_overrides.return_value = {
            "model": "claude-opus-4-20250514",
            "max_iterations": 50,
        }
        action = _make_thinking_action()
        kwargs = action._build_model_kwargs(skill_action=skill)
        assert kwargs["model"] == "claude-opus-4-20250514"

    def test_build_model_kwargs_thinking_and_skill(self):
        skill = MagicMock()
        skill.get_model_overrides.return_value = {"max_tokens": 16000}
        action = _make_thinking_action(thinking_budget_tokens=5000)
        kwargs = action._build_model_kwargs(skill_action=skill)
        assert "thinking" in kwargs
        assert kwargs["max_tokens"] >= 5001


class TestThinkingInteractActionMessages:
    """Test message building and truncation."""

    @pytest.mark.asyncio
    async def test_build_initial_messages_no_skill(self):
        action = _make_thinking_action()
        visitor = MagicMock()
        visitor.utterance = "Review this code"

        messages = await action._build_initial_messages(visitor, skill_action=None)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Review this code" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_build_initial_messages_with_skill(self):
        skill = MagicMock()
        skill.compose_system_prompt.return_value = "You are a code reviewer."
        skill.compose_utterance.return_value = "[Review mode] Review this code"
        action = _make_thinking_action()
        visitor = MagicMock()
        visitor.utterance = "Review this code"

        messages = await action._build_initial_messages(visitor, skill_action=skill)
        assert messages[0]["content"] == "You are a code reviewer."
        assert "[Review mode]" in messages[1]["content"]

    def test_maybe_truncate_messages_short_list(self):
        action = _make_thinking_action(max_full_tool_results=5)
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "content": "result1"},
            {"role": "tool", "tool_call_id": "2", "content": "result2"},
        ]
        result = action._maybe_truncate_messages(messages)
        assert len(result) == 5

    def test_maybe_truncate_messages_long_list(self):
        action = _make_thinking_action(max_full_tool_results=2)
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ]
        # Add many tool results
        for i in range(10):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"tc_{i}",
                    "content": f"Long result {i}" * 50,
                }
            )

        result = action._maybe_truncate_messages(messages)
        # Some older results should be summarized
        summarized = [m for m in result if "summarized" in m.get("content", "")]
        # Last 2 should be kept in full
        full_results = [
            m
            for m in result
            if m.get("role") == "tool" and "summarized" not in m.get("content", "")
        ]
        assert len(full_results) <= 2


class TestThinkingInteractActionAssistantContent:
    """Test assistant content block building."""

    def test_build_assistant_content_text_only(self):
        action = _make_thinking_action()
        model_result = MagicMock()
        model_result.tool_calls = []
        model_result.response = "Hello there"
        model_result.provider = "openai"

        msg = action._build_assistant_content(model_result)
        assert msg == {"role": "assistant", "content": "Hello there"}

    def test_build_assistant_content_with_tool_calls(self):
        action = _make_thinking_action()
        model_result = MagicMock()
        model_result.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
            }
        ]
        model_result.response = ""
        model_result.provider = "openai"

        msg = action._build_assistant_content(model_result)
        assert msg["role"] == "assistant"
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["function"]["name"] == "read_file"

    def test_build_assistant_content_anthropic_format(self):
        action = _make_thinking_action()
        model_result = MagicMock()
        model_result.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
            }
        ]
        model_result.response = ""
        model_result.provider = "anthropic"

        msg = action._build_assistant_content(model_result)
        assert msg["role"] == "assistant"
        content_blocks = msg["content"]
        tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
        assert len(tool_use_blocks) == 1
        assert tool_use_blocks[0]["name"] == "read_file"

    def test_parse_tool_arguments_dict(self):
        action = _make_thinking_action()
        assert action._parse_tool_arguments({"key": "val"}) == {"key": "val"}

    def test_parse_tool_arguments_string(self):
        action = _make_thinking_action()
        assert action._parse_tool_arguments('{"key": "val"}') == {"key": "val"}

    def test_parse_tool_arguments_invalid(self):
        action = _make_thinking_action()
        assert action._parse_tool_arguments("not json") == {}


class TestThinkingInteractActionHealthcheck:
    """Test healthcheck validation."""

    @pytest.mark.asyncio
    async def test_healthcheck_valid(self):
        action = _make_thinking_action()
        result = await action.healthcheck()
        assert result is True

    @pytest.mark.asyncio
    async def test_healthcheck_no_model_type(self):
        action = _make_thinking_action()
        action.model_action_type = ""
        result = await action.healthcheck()
        assert result is False

    @pytest.mark.asyncio
    async def test_healthcheck_invalid_iterations(self):
        action = _make_thinking_action()
        action.max_iterations = 0
        result = await action.healthcheck()
        assert result is False


# --- Helpers ---


def _make_thinking_action(**kwargs):
    """Create a ThinkingInteractAction-like object for testing without graph persistence."""
    action = MagicMock(spec=ThinkingInteractAction)

    # Set defaults from the class attributes
    action.weight = kwargs.get("weight", -60)
    action.max_iterations = kwargs.get("max_iterations", 25)
    action.max_duration_seconds = kwargs.get("max_duration_seconds", 300.0)
    action.thinking_budget_tokens = kwargs.get("thinking_budget_tokens", 0)
    action.model_action_type = kwargs.get(
        "model_action_type", "AnthropicLanguageModelAction"
    )
    action.model = kwargs.get("model", "claude-sonnet-4-20250514")
    action.model_temperature = kwargs.get("model_temperature", 0.3)
    action.model_max_tokens = kwargs.get("model_max_tokens", 8192)
    action.skill = kwargs.get("skill", None)
    action.tool_servers = kwargs.get("tool_servers", [])
    action.allow_local_tools = kwargs.get("allow_local_tools", False)
    action.stream_thinking = kwargs.get("stream_thinking", True)
    action.stream_tool_progress = kwargs.get("stream_tool_progress", True)
    action.max_full_tool_results = kwargs.get("max_full_tool_results", 10)

    # Wire up real methods
    action._build_model_kwargs = (
        lambda skill_action=None: ThinkingInteractAction._build_model_kwargs(
            action, skill_action
        )
    )
    action._build_initial_messages = (
        lambda v, skill_action=None: ThinkingInteractAction._build_initial_messages(
            action, v, skill_action
        )
    )
    action._maybe_truncate_messages = (
        lambda msgs: ThinkingInteractAction._maybe_truncate_messages(action, msgs)
    )
    action._build_assistant_content = (
        lambda mr: ThinkingInteractAction._build_assistant_content(action, mr)
    )
    action._parse_tool_arguments = (
        lambda args: ThinkingInteractAction._parse_tool_arguments(action, args)
    )

    async def _healthcheck():
        return await ThinkingInteractAction.healthcheck(action)

    action.healthcheck = _healthcheck

    return action
