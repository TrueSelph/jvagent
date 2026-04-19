"""Tests for LoopContext: message building, truncation, format conversion."""

import json

import pytest

from jvagent.action.skill.loop_context import LoopContext, LoopContextConfig


def _make_loop_context(**kwargs):
    config = LoopContextConfig(**kwargs)
    return LoopContext(config)


# --- build_initial_messages ---


class TestBuildInitialMessages:
    @pytest.mark.asyncio
    async def test_basic_construction(self):
        ctx = _make_loop_context()
        messages = await ctx.build_initial_messages(
            system_prompt="You are an assistant.",
            utterance="Hello",
            conversation=None,
            interaction=None,
        )
        assert messages[0]["role"] == "system"
        assert "You are an assistant." in messages[0]["content"]
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_with_skill_index_section(self):
        ctx = _make_loop_context()
        messages = await ctx.build_initial_messages(
            system_prompt="You are an assistant.",
            utterance="Hello",
            conversation=None,
            interaction=None,
            skill_index_section="## Skills\n- gmail",
        )
        assert "## Skills" in messages[0]["content"]
        assert "gmail" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_with_conversation_history(self):
        from unittest.mock import AsyncMock, MagicMock

        conversation = MagicMock()
        conversation.get_interaction_history = AsyncMock(
            return_value=[
                {"role": "user", "content": "Prior utterance"},
                {"role": "assistant", "content": "Prior response"},
            ]
        )
        interaction = MagicMock()
        interaction.id = "current-id"

        ctx = _make_loop_context(history_limit=5)
        messages = await ctx.build_initial_messages(
            system_prompt="System",
            utterance="New utterance",
            conversation=conversation,
            interaction=interaction,
        )
        # system + 2 history + user = 4
        assert len(messages) == 4
        assert messages[1]["content"] == "Prior utterance"

    @pytest.mark.asyncio
    async def test_handles_conversation_history_error(self):
        from unittest.mock import AsyncMock, MagicMock

        conversation = MagicMock()
        conversation.get_interaction_history = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        interaction = MagicMock()
        interaction.id = "current-id"

        ctx = _make_loop_context()
        messages = await ctx.build_initial_messages(
            system_prompt="System",
            utterance="Hello",
            conversation=conversation,
            interaction=interaction,
        )
        # system + user = 2 (history skipped)
        assert len(messages) == 2


# --- maybe_truncate ---


class TestMaybeTruncate:
    def test_no_truncation_when_under_limit(self):
        ctx = _make_loop_context(max_full_tool_results=10)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = ctx.maybe_truncate(messages)
        assert result == messages

    def test_truncates_old_tool_results_openai_format(self):
        ctx = _make_loop_context(max_full_tool_results=1)
        # Need > max_full_tool_results * 2 + 4 = 6 messages to trigger truncation
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [{"id": "1", "function": {"name": "t"}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "old result 1"},
            {
                "role": "assistant",
                "content": "hi2",
                "tool_calls": [{"id": "2", "function": {"name": "t"}}],
            },
            {"role": "tool", "tool_call_id": "2", "content": "old result 2"},
            {
                "role": "assistant",
                "content": "hi3",
                "tool_calls": [{"id": "3", "function": {"name": "t"}}],
            },
            {"role": "tool", "tool_call_id": "3", "content": "recent result"},
        ]
        result = ctx.maybe_truncate(messages)
        summarized = [
            m
            for m in result
            if isinstance(m.get("content"), str)
            and "summarized" in m.get("content", "")
        ]
        assert len(summarized) >= 1

    def test_truncates_old_tool_results_anthropic_format(self):
        ctx = _make_loop_context(max_full_tool_results=1)
        # Build messages with enough Anthropic-format tool results to trigger truncation
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "1", "name": "t", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "1", "content": "old1"}
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "2", "name": "t", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "2", "content": "old2"}
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "3", "name": "t", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "3", "content": "recent"}
                ],
            },
        ]
        result = ctx.maybe_truncate(messages)
        # Anthropic-format tool results in user messages get detected and summarized
        summarized = [
            m
            for m in result
            if isinstance(m.get("content"), str)
            and "summarized" in m.get("content", "")
        ]
        assert len(summarized) >= 1

    def test_truncates_individual_long_tool_results(self):
        # Individual truncation happens when a kept tool result exceeds
        # max_tool_result_tokens. Need enough total messages to enter truncation.
        ctx = _make_loop_context(
            max_full_tool_results=2,
            max_tool_result_tokens=1,
            tool_result_truncation_chars=10,
        )
        long_content = "x" * 500
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        # Add enough tool result pairs to exceed the threshold
        for i in range(5):
            messages.append(
                {
                    "role": "assistant",
                    "content": "hi",
                    "tool_calls": [{"id": str(i), "function": {"name": "t"}}],
                }
            )
            content = long_content if i == 4 else "short"
            messages.append(
                {"role": "tool", "tool_call_id": str(i), "content": content}
            )
        # messages length = 12, threshold = 2*2+4 = 8, so truncation triggers
        result = ctx.maybe_truncate(messages)
        # The last tool result (long) should be individually truncated
        tool_msgs = [
            m
            for m in result
            if m.get("role") == "tool" and "truncated" in m.get("content", "")
        ]
        assert len(tool_msgs) >= 1


# --- convert_for_provider ---


class TestConvertForProvider:
    def test_openai_format_unchanged(self):
        messages = [
            {"role": "system", "content": "hi"},
            {"role": "user", "content": "hello"},
        ]
        result = LoopContext.convert_for_provider(messages, "openai")
        assert result == messages

    def test_anthropic_tool_calls_converted(self):
        messages = [
            {
                "role": "assistant",
                "content": "Let me check",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "function": {"name": "search", "arguments": '{"q": "test"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "result data"},
        ]
        result = LoopContext.convert_for_provider(messages, "anthropic")
        # Assistant should have content blocks
        assistant_msg = result[0]
        assert assistant_msg["role"] == "assistant"
        assert isinstance(assistant_msg["content"], list)
        types = [b["type"] for b in assistant_msg["content"]]
        assert "tool_use" in types
        # Tool result should be merged into user message
        user_msg = result[1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0]["type"] == "tool_result"

    def test_anthropic_consecutive_tool_results_merged(self):
        messages = [
            {"role": "tool", "tool_call_id": "1", "content": "a"},
            {"role": "tool", "tool_call_id": "2", "content": "b"},
        ]
        result = LoopContext.convert_for_provider(messages, "anthropic")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) == 2

    def test_anthropic_non_tool_messages_passthrough(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        result = LoopContext.convert_for_provider(messages, "anthropic")
        assert result == messages


# --- build_assistant_content ---


class TestBuildAssistantContent:
    def test_text_only(self):
        from unittest.mock import MagicMock

        mr = MagicMock()
        mr.tool_calls = None
        mr.response = "Hello there"
        result = LoopContext.build_assistant_content(mr)
        assert result == {"role": "assistant", "content": "Hello there"}

    def test_openai_with_tool_calls(self):
        from unittest.mock import MagicMock

        mr = MagicMock()
        mr.tool_calls = [{"id": "1", "function": {"name": "search", "arguments": "{}"}}]
        mr.response = "Let me search"
        mr.provider = "openai"
        result = LoopContext.build_assistant_content(mr)
        assert result["role"] == "assistant"
        assert result["content"] == "Let me search"
        assert result["tool_calls"] == mr.tool_calls

    def test_anthropic_with_tool_calls(self):
        from unittest.mock import MagicMock

        mr = MagicMock()
        mr.tool_calls = [
            {"id": "1", "function": {"name": "search", "arguments": '{"q": "test"}'}}
        ]
        mr.response = "Let me search"
        mr.provider = "anthropic"
        result = LoopContext.build_assistant_content(mr)
        assert isinstance(result["content"], list)
        types = [b["type"] for b in result["content"]]
        assert "text" in types
        assert "tool_use" in types

    def test_no_response_no_tool_calls(self):
        from unittest.mock import MagicMock

        mr = MagicMock()
        mr.tool_calls = None
        mr.response = ""
        result = LoopContext.build_assistant_content(mr)
        assert result == {"role": "assistant", "content": ""}


# --- parse_tool_arguments ---


class TestParseToolArguments:
    def test_dict_passthrough(self):
        args = {"key": "value"}
        assert LoopContext.parse_tool_arguments(args) == args

    def test_json_string(self):
        assert LoopContext.parse_tool_arguments('{"key": "value"}') == {"key": "value"}

    def test_invalid_json_string(self):
        assert LoopContext.parse_tool_arguments("not json") == {}

    def test_non_string_non_dict(self):
        assert LoopContext.parse_tool_arguments(42) == {}

    def test_none_input(self):
        assert LoopContext.parse_tool_arguments(None) == {}
