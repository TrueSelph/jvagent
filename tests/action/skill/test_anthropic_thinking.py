"""Tests for Anthropic extended thinking support in the provider."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.model.language.anthropic.anthropic import (
    AnthropicLanguageModelAction,
)


class TestAnthropicBuildPayloadThinking:
    """Test _build_payload with extended thinking."""

    def test_build_payload_without_thinking(self):
        action = _make_anthropic_action()
        payload = action._build_payload(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
        )
        assert "thinking" not in payload
        assert "temperature" in payload

    def test_build_payload_with_thinking(self):
        action = _make_anthropic_action()
        payload = action._build_payload(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            thinking={"type": "enabled", "budget_tokens": 5000},
        )
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 5000}
        # Temperature should be omitted when thinking is enabled
        assert "temperature" not in payload
        # max_tokens should be >= budget_tokens + 1
        assert payload["max_tokens"] >= 5001

    def test_build_payload_thinking_adjusts_max_tokens(self):
        action = _make_anthropic_action()
        action.max_tokens = 100  # Too low for thinking
        payload = action._build_payload(
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
            thinking={"type": "enabled", "budget_tokens": 5000},
        )
        assert payload["max_tokens"] >= 5001


class TestAnthropicExtractResultFieldsThinking:
    """Test _extract_result_fields with thinking blocks."""

    def test_extract_result_fields_with_thinking_blocks(self):
        action = _make_anthropic_action()
        data = {
            "content": [
                {"type": "thinking", "thinking": "Let me analyze this..."},
                {"type": "text", "text": "The answer is 42"},
            ],
            "stop_reason": "end_turn",
        }
        text, tool_calls, finish_reason = action._extract_result_fields(data)
        # Thinking blocks should NOT be in text
        assert "analyze" not in text
        assert "42" in text
        assert finish_reason == "end_turn"

    def test_extract_result_fields_with_tool_use(self):
        action = _make_anthropic_action()
        data = {
            "content": [
                {"type": "text", "text": "I'll look that up"},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "search",
                    "input": {"q": "test"},
                },
            ],
            "stop_reason": "tool_use",
        }
        text, tool_calls, finish_reason = action._extract_result_fields(data)
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "search"


class TestModelActionResultThinking:
    """Test ModelActionResult thinking attributes."""

    def test_model_action_result_thinking_defaults(self):
        from jvagent.action.model.language.base import ModelActionResult

        result = ModelActionResult(response="hello")
        assert result.thinking_content is None
        assert result.thinking_tokens is None

    def test_model_action_result_thinking_set(self):
        from jvagent.action.model.language.base import ModelActionResult

        result = ModelActionResult(
            response="hello",
            thinking_content="Let me think...",
            thinking_tokens=500,
        )
        assert result.thinking_content == "Let me think..."
        assert result.thinking_tokens == 500


# --- Helpers ---


def _make_anthropic_action():
    """Create an AnthropicLanguageModelAction for testing without persistence."""
    action = MagicMock(spec=AnthropicLanguageModelAction)
    action.model = "claude-sonnet-4-20250514"
    action.api_endpoint = "https://api.anthropic.com/v1"
    action.anthropic_version = "2023-06-01"
    action.max_tokens = 8192
    action.temperature = 0.3
    action.top_p = 1.0
    action.provider = "anthropic"

    # Wire up real methods
    action._build_payload = lambda messages, tools=None, stream=False, **kwargs: AnthropicLanguageModelAction._build_payload(
        action, messages, tools, stream, **kwargs
    )
    action._extract_result_fields = (
        lambda data: AnthropicLanguageModelAction._extract_result_fields(action, data)
    )
    action._extract_system_and_messages = (
        lambda messages: AnthropicLanguageModelAction._extract_system_and_messages(
            action, messages
        )
    )
    action._normalize_content = (
        lambda content: AnthropicLanguageModelAction._normalize_content(action, content)
    )
    action._map_tools = lambda tools: AnthropicLanguageModelAction._map_tools(
        action, tools
    )

    return action
