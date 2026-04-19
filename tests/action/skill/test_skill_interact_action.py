"""Tests for SkillInteractAction: prompts, loop helpers, and skill discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_interact_action import SkillInteractAction


class TestSkillInteractActionModelKwargs:
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

    def test_build_model_kwargs_thinking(self):
        action = _make_thinking_action(thinking_budget_tokens=5000)
        kwargs = action._build_model_kwargs()
        assert "thinking" in kwargs
        assert kwargs["max_tokens"] >= 5001


class TestSkillInteractActionMessages:
    """Test message building and truncation."""

    @pytest.mark.asyncio
    async def test_build_initial_messages(self):
        action = _make_thinking_action()
        visitor = MagicMock()
        visitor.utterance = "Review this code"

        messages = await action._build_initial_messages(visitor)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Review this code" in messages[1]["content"]

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


class TestSkillInteractActionAssistantContent:
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


class TestSkillInteractActionProviderConversion:
    def test_convert_messages_for_anthropic(self):
        action = _make_thinking_action()
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "read_skill",
                            "arguments": '{"skill_name":"example_skill"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "result content",
            },
        ]
        converted = action._convert_messages_for_provider(messages, "anthropic")
        assert converted[2]["role"] == "assistant"
        assert any(block.get("type") == "tool_use" for block in converted[2]["content"])
        assert converted[3]["role"] == "user"
        assert converted[3]["content"][0]["type"] == "tool_result"

    def test_convert_messages_passthrough_non_anthropic(self):
        action = _make_thinking_action()
        messages = [{"role": "user", "content": "hello"}]
        assert action._convert_messages_for_provider(messages, "openai") == messages


class TestSkillInteractActionTermination:
    @pytest.mark.asyncio
    async def test_force_termination_makes_final_call_without_thinking(self):
        action = _make_thinking_action()
        model_result = MagicMock()
        model_result.get_response = AsyncMock(return_value="Final answer")
        model_result.response = "Final answer"
        action._call_model = AsyncMock(return_value=model_result)

        messages = [{"role": "user", "content": "Do work"}]
        model_kwargs = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "thinking": {"type": "enabled", "budget_tokens": 2000},
        }
        result = await action._force_termination(
            messages=messages,
            tools=[],
            visitor=MagicMock(),
            model_kwargs=model_kwargs,
        )
        assert result == "Final answer"
        call_args = action._call_model.await_args
        assert call_args.args[1] is None
        assert "thinking" not in call_args.args[3]


class TestSkillInteractActionSkillDiscovery:
    @pytest.mark.asyncio
    async def test_discover_skill_bundles_uses_resolver(self):
        action = _make_thinking_action(skills="-all", skills_source="both")
        visitor = MagicMock()
        visitor._agent = SimpleNamespace(namespace="demo", name="assistant")
        with patch(
            "jvagent.action.skill.skill_interact_action.resolve_merged_skill_bundles",
            return_value={"resolved_skill": {"description": "Resolved by resolver"}},
        ) as mocked_resolver:
            discovered = await action._discover_skill_bundles(visitor)
        assert "resolved_skill" in discovered
        mocked_resolver.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_skill_bundles_from_agent_dir(self, tmp_path, monkeypatch):
        app_root = tmp_path
        monkeypatch.chdir(app_root)
        skill_dir = app_root / "agents" / "demo" / "assistant" / "skills" / "audit"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: audit_skill
description: Perform audits.
allowed-tools:
  - summarize_findings
---

Use this workflow.
""",
            encoding="utf-8",
        )
        (skill_dir / "summarize_findings.py").write_text(
            """
def get_tool_definition():
    return {
        "name": "summarize_findings",
        "description": "Summarize findings",
        "parameters": {"type": "object", "properties": {}}
    }

async def execute(arguments):
    return "ok"
""",
            encoding="utf-8",
        )

        action = _make_thinking_action(skills="-all", skills_source="app")
        visitor = MagicMock()
        visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

        discovered = await action._discover_skill_bundles(visitor)
        assert "audit_skill" in discovered
        assert discovered["audit_skill"]["description"] == "Perform audits."
        assert discovered["audit_skill"]["allowed_tools"] == ["summarize_findings"]
        assert discovered["audit_skill"]["tool_files"]


class TestSkillInteractActionHealthcheck:
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


class TestSkillInteractActionThoughtPublishing:
    @pytest.mark.asyncio
    async def test_run_agentic_loop_publishes_structured_thoughts(self):
        action = _make_thinking_action()
        action.publish_thought = AsyncMock()
        action.relay_thoughts_to_channels = False

        first_result = MagicMock()
        first_result.thinking_content = "Need to inspect files"
        first_result.thinking_tokens = 12
        first_result.tool_calls = [
            {
                "id": "tc_1",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ]
        first_result.response = ""
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(return_value="")

        second_result = MagicMock()
        second_result.thinking_content = None
        second_result.thinking_tokens = 0
        second_result.tool_calls = []
        second_result.response = "Final answer"
        second_result.provider = "openai"
        second_result.get_response = AsyncMock(return_value="Final answer")

        action._call_model = AsyncMock(side_effect=[first_result, second_result])

        visitor = MagicMock()
        visitor.utterance = "Please investigate and summarize"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(
            return_value=[
                {
                    "role": "tool",
                    "tool_call_id": "tc_1",
                    "content": "file read complete",
                }
            ]
        )

        task_handle = MagicMock()
        task_handle.record_step = AsyncMock()

        response, termination_reason = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        assert response == "Final answer"
        assert termination_reason == "completed"

        thought_types = [
            call.kwargs.get("thought_type")
            for call in action.publish_thought.await_args_list
        ]
        segment_ids = [
            call.kwargs.get("segment_id")
            for call in action.publish_thought.await_args_list
        ]
        assert "reasoning" in thought_types
        assert "tool_call" in thought_types
        assert "tool_result" in thought_types
        assert "iter-1-reasoning" in segment_ids
        assert "iter-1-call-read_file-0" in segment_ids
        assert "iter-1-result-tc_1" in segment_ids

    @pytest.mark.asyncio
    async def test_run_agentic_loop_publishes_intermediate_user_text(self):
        """Mid-loop assistant text alongside tool_calls must be published as
        category='user' so it is rendered AND committed to interaction.response."""
        action = _make_thinking_action()
        action.publish = AsyncMock()
        action.publish_thought = AsyncMock()
        action.relay_thoughts_to_channels = False

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = [
            {"id": "tc_1", "function": {"name": "read_file", "arguments": "{}"}}
        ]
        first_result.response = "Sure, let me look that up for you."
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(return_value="")

        second_result = MagicMock()
        second_result.thinking_content = None
        second_result.thinking_tokens = 0
        second_result.tool_calls = []
        second_result.response = "Final answer"
        second_result.provider = "openai"
        second_result.get_response = AsyncMock(return_value="Final answer")

        action._call_model = AsyncMock(side_effect=[first_result, second_result])

        visitor = MagicMock()
        visitor.utterance = "look it up"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(
            return_value=[
                {"role": "tool", "tool_call_id": "tc_1", "content": "ok"},
            ]
        )

        task_handle = MagicMock()
        task_handle.record_step = AsyncMock()

        await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        published_user_messages = [
            call.kwargs.get("content") for call in action.publish.await_args_list
        ]
        assert "Sure, let me look that up for you." in published_user_messages
        # The intermediate publish must not be flagged transient (default False)
        # so it is committed to interaction.response.
        intermediate_calls = [
            call
            for call in action.publish.await_args_list
            if call.kwargs.get("content") == "Sure, let me look that up for you."
        ]
        assert intermediate_calls, "intermediate user text was not published"
        assert intermediate_calls[0].kwargs.get("transient") in (False, None)

    @pytest.mark.asyncio
    async def test_run_agentic_loop_skips_intermediate_when_disabled(self):
        action = _make_thinking_action(commit_intermediate_messages=False)
        action.publish = AsyncMock()
        action.publish_thought = AsyncMock()

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = [
            {"id": "tc_1", "function": {"name": "read_file", "arguments": "{}"}}
        ]
        first_result.response = "preface"
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(return_value="")

        second_result = MagicMock()
        second_result.thinking_content = None
        second_result.thinking_tokens = 0
        second_result.tool_calls = []
        second_result.response = "Final"
        second_result.provider = "openai"
        second_result.get_response = AsyncMock(return_value="Final")

        action._call_model = AsyncMock(side_effect=[first_result, second_result])

        visitor = MagicMock()
        visitor.utterance = "x"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(
            return_value=[{"role": "tool", "tool_call_id": "tc_1", "content": "ok"}]
        )

        task_handle = MagicMock()
        task_handle.record_step = AsyncMock()

        await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )
        published_user_messages = [
            call.kwargs.get("content") for call in action.publish.await_args_list
        ]
        assert "preface" not in published_user_messages

    @pytest.mark.asyncio
    async def test_run_agentic_loop_with_single_iteration_makes_model_call(self):
        action = _make_thinking_action(max_iterations=1)

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = []
        first_result.response = "Done in one step"
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(return_value="Done in one step")
        action._call_model = AsyncMock(return_value=first_result)

        visitor = MagicMock()
        visitor.utterance = "quick task"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(return_value=[])

        task_handle = MagicMock()
        task_handle.record_step = AsyncMock()

        response, termination_reason = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        assert response == "Done in one step"
        assert termination_reason == "completed"
        action._call_model.assert_awaited_once()


# --- Helpers ---


def _make_thinking_action(**kwargs):
    """Create a SkillInteractAction-like object for testing without graph persistence."""
    action = MagicMock(spec=SkillInteractAction)

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
    action.skills = kwargs.get("skills", None)
    action.denied_skills = kwargs.get("denied_skills", [])
    action.skills_source = kwargs.get("skills_source", "both")
    action.tool_servers = kwargs.get("tool_servers", [])
    action.allow_local_tools = kwargs.get("allow_local_tools", False)
    action.stream_thinking = kwargs.get("stream_thinking", True)
    action.stream_tool_progress = kwargs.get("stream_tool_progress", True)
    action.commit_intermediate_messages = kwargs.get(
        "commit_intermediate_messages", True
    )
    action.relay_thoughts_to_channels = kwargs.get("relay_thoughts_to_channels", False)
    action.max_full_tool_results = kwargs.get("max_full_tool_results", 10)
    action.max_tool_result_tokens = kwargs.get("max_tool_result_tokens", 400)
    action.tool_result_truncation_chars = kwargs.get(
        "tool_result_truncation_chars", 500
    )
    action.history_limit = kwargs.get("history_limit", 5)
    action.call_timeout_seconds = kwargs.get("call_timeout_seconds", 60.0)
    action.task_sync_every_steps = kwargs.get("task_sync_every_steps", 3)

    # Wire up real methods
    action._build_model_kwargs = lambda: SkillInteractAction._build_model_kwargs(action)
    action._build_initial_messages = (
        lambda v, discovered_skills=None: SkillInteractAction._build_initial_messages(
            action, v, discovered_skills
        )
    )
    action._maybe_truncate_messages = (
        lambda msgs: SkillInteractAction._maybe_truncate_messages(action, msgs)
    )
    action._build_assistant_content = (
        lambda mr: SkillInteractAction._build_assistant_content(action, mr)
    )
    action._convert_messages_for_provider = (
        lambda messages, provider: SkillInteractAction._convert_messages_for_provider(
            action, messages, provider
        )
    )
    action._force_termination = lambda messages, tools, visitor, model_kwargs: SkillInteractAction._force_termination(
        action, messages, tools, visitor, model_kwargs
    )
    action._discover_skill_bundles = (
        lambda visitor: SkillInteractAction._discover_skill_bundles(action, visitor)
    )
    action._run_agentic_loop = lambda visitor, tool_executor, task_handle, discovered_skills=None: SkillInteractAction._run_agentic_loop(
        action, visitor, tool_executor, task_handle, discovered_skills
    )
    action._parse_tool_arguments = (
        lambda args: SkillInteractAction._parse_tool_arguments(action, args)
    )

    async def _healthcheck():
        return await SkillInteractAction.healthcheck(action)

    action.healthcheck = _healthcheck

    return action
