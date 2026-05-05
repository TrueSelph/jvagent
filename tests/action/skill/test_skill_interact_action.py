"""Tests for SkillInteractAction: prompts, loop helpers, and skill discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.context_compactor import CompactorConfig, ContextCompactor
from jvagent.action.skill.loop_context import LoopContext, LoopContextConfig
from jvagent.action.skill.skill_action import SkillAction
from jvagent.action.skill.skill_action_contracts import SkillRunConfig, SkillRunContext
from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.skill_interact_action import SkillInteractAction
from jvagent.action.skill.stuck_detector import StuckDetector
from jvagent.memory.evidence_log import EvidenceLog


def _agentic_call_model_side_effect(*results):
    """Build an async side_effect for mocked ``_call_model`` (SkillAction signature).

    The side_effect emits reasoning thoughts via ``ctx.publish_callback`` when
    ``thinking_content`` is set, mirroring what the real ``_call_model`` does
    during streaming.
    """
    idx = 0

    async def _fn(
        messages,
        tools,
        ctx,
        base_model_kwargs,
        reasoning_cfg,
        *,
        profile="reasoning",
        loop_iteration=None,
    ):
        nonlocal idx
        if loop_iteration is None:
            r = results[-1]
            if getattr(r, "get_response", None):
                await r.get_response()
            return r
        r = results[idx]
        idx += 1
        if ctx.publish_callback and getattr(r, "thinking_content", None):
            await ctx.publish_callback(
                r.thinking_content,
                category="thought",
                thought_type="reasoning",
                segment_id=f"iter-{loop_iteration}-reasoning",
                streaming_complete=False,
                relay_to_adapters=False,
            )
            await ctx.publish_callback(
                "",
                category="thought",
                thought_type="reasoning",
                segment_id=f"iter-{loop_iteration}-reasoning",
                streaming_complete=True,
                relay_to_adapters=False,
            )
        if getattr(r, "get_response", None):
            await r.get_response()
        return r

    return _fn


class TestSkillInteractHelpers:
    """Unit tests for static helpers and guard methods."""

    def test_resolve_thinking_token_count(self):
        m = MagicMock()
        m.thinking_tokens = 42
        assert SkillAction._resolve_thinking_token_count(m) == 42
        m2 = MagicMock()
        m2.thinking_tokens = 0
        m2.thinking_content = "x" * 100
        m2.metrics = {}
        assert SkillAction._resolve_thinking_token_count(m2) == 25

    def test_should_prefer_best_over_degenerate(self):
        action = _make_thinking_action()
        long_b = "Here is a detailed list of all seven skills with descriptions. " * 3
        assert action._should_prefer_best_over_candidate("Understood.", long_b) is True

    def test_format_persona_directive_includes_utterance(self):
        d = SkillInteractAction._format_persona_directive(
            "What are your skills", "A: 1, B: 2"
        )
        assert "What are your skills" in d
        assert "A: 1, B: 2" in d


class TestSkillInteractActionModelKwargs:
    """Test generic reasoning config building."""

    def test_build_reasoning_model_config_defaults(self):
        run_cfg = SkillRunConfig()
        rcfg = SkillAction._build_reasoning_cfg(run_cfg)
        assert rcfg.reasoning_budget_tokens == 0
        assert rcfg.reasoning_effort is None
        assert rcfg.reasoning_enabled is None

    def test_build_reasoning_model_config_with_budget(self):
        run_cfg = SkillRunConfig(reasoning_budget_tokens=5000)
        rcfg = SkillAction._build_reasoning_cfg(run_cfg)
        assert rcfg.reasoning_budget_tokens == 5000
        assert rcfg.reasoning_enabled is True

    def test_build_reasoning_model_config_legacy_budget(self):
        # thinking_budget_tokens mapped to reasoning_budget_tokens in _make_thinking_action
        action = _make_thinking_action(thinking_budget_tokens=5000)
        rcfg = action._build_reasoning_model_config()
        assert rcfg.reasoning_budget_tokens == 5000
        assert rcfg.reasoning_enabled is True

    def test_build_reasoning_model_config_with_reasoning_extra(self):
        run_cfg = SkillRunConfig(reasoning_extra={"effort": "medium"})
        rcfg = SkillAction._build_reasoning_cfg(run_cfg)
        assert rcfg.reasoning_extra == {"effort": "medium"}


class TestSkillInteractActionMessages:
    """Test message building and truncation."""

    @pytest.mark.asyncio
    async def test_build_initial_messages(self):
        ctx = LoopContext(LoopContextConfig())
        messages = await ctx.build_initial_messages(
            system_prompt="You are an agent.",
            utterance="Review this code",
            conversation=None,
            interaction=None,
        )
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
        # Some older results should be summarized (ContextCompactor: British spelling)
        summarized = [m for m in result if "summarised" in m.get("content", "")]
        assert len(summarized) >= 1
        # Last 2 should be kept in full
        full_results = [
            m
            for m in result
            if m.get("role") == "tool" and "summarised" not in m.get("content", "")
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
    async def test_force_termination_uses_final_profile(self):
        run_cfg = SkillRunConfig()
        sa = SkillAction()

        model_result = MagicMock()
        model_result.get_response = AsyncMock(return_value="Final answer")
        model_result.response = "Final answer"

        mock_call_model = AsyncMock(return_value=model_result)
        sa._call_model = mock_call_model  # type: ignore[method-assign]

        ctx = SkillRunContext(
            utterance="Do work",
            conversation=None,
            model_action=MagicMock(),
            task_store=None,
            config=run_cfg,
        )
        messages = [{"role": "user", "content": "Do work"}]
        base_model_kwargs = {"model": "claude-sonnet-4-20250514", "max_tokens": 4096}
        reasoning_cfg = SkillAction._build_reasoning_cfg(run_cfg)

        result = await sa._force_termination(
            messages, [], ctx, base_model_kwargs, reasoning_cfg
        )

        assert result == "Final answer"
        call_args = mock_call_model.await_args
        assert call_args.args[1] is None  # tools=None
        assert call_args.kwargs.get("profile") == "final"
        assert call_args.kwargs.get("loop_iteration") is None


class TestSkillInteractActionSkillDiscovery:
    @pytest.mark.asyncio
    async def test_discover_skill_bundles_uses_resolver(self):
        visitor = MagicMock()
        visitor._agent = SimpleNamespace(namespace="demo", name="assistant")
        with patch(
            "jvagent.action.skill.skill_catalog.resolve_merged_skill_bundles",
            return_value={"resolved_skill": {"description": "Resolved by resolver"}},
        ) as mocked_resolver:
            catalog = await SkillCatalog.discover(
                visitor=visitor,
                skills_selector="-all",
                skills_source="both",
            )
        assert "resolved_skill" in catalog.skills
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

        visitor = MagicMock()
        visitor._agent = SimpleNamespace(namespace="demo", name="assistant")

        catalog = await SkillCatalog.discover(
            visitor=visitor,
            skills_selector="-all",
            skills_source="app",
        )
        assert "audit_skill" in catalog.skills
        assert catalog.skills["audit_skill"]["description"] == "Perform audits."
        assert catalog.skills["audit_skill"]["allowed_tools"] == ["summarize_findings"]
        assert catalog.skills["audit_skill"]["tool_files"]


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

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result, second_result)
        )

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
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        (
            response,
            termination_reason,
            stuck_corrections,
            result_attributions,
        ) = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        assert response == "Final answer"
        assert termination_reason == "completed"
        assert stuck_corrections == 0

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
    async def test_run_agentic_loop_publishes_intermediate_reasoning_thought(self):
        """Mid-loop assistant text alongside tool_calls should stay in the
        reasoning stream instead of being committed to the user channel."""
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

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result, second_result)
        )

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
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        published_thought_messages = [
            call.kwargs.get("content")
            for call in action.publish_thought.await_args_list
        ]
        assert "Sure, let me look that up for you." in published_thought_messages
        intermediate_calls = [
            call
            for call in action.publish_thought.await_args_list
            if call.kwargs.get("content") == "Sure, let me look that up for you."
        ]
        assert intermediate_calls, "intermediate reasoning text was not published"
        assert intermediate_calls[0].kwargs.get("thought_type") == "reasoning"
        published_user_messages = [
            call.kwargs.get("content") for call in action.publish.await_args_list
        ]
        assert "Sure, let me look that up for you." not in published_user_messages

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

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result, second_result)
        )

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
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

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
        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result)
        )

        visitor = MagicMock()
        visitor.utterance = "quick task"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(return_value=[])

        task_handle = MagicMock()
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        (
            response,
            termination_reason,
            stuck_corrections,
            result_attributions,
        ) = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=None,
        )

        assert response == "Done in one step"
        assert termination_reason == "completed"
        assert stuck_corrections == 0
        action._call_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_agentic_loop_retries_once_for_skill_first_when_skills_available(
        self,
    ):
        action = _make_thinking_action(max_iterations=3, skill_first_retry_limit=1)

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = []
        first_result.response = "Direct answer without skills"
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(
            return_value="Direct answer without skills"
        )

        second_result = MagicMock()
        second_result.thinking_content = None
        second_result.thinking_tokens = 0
        second_result.tool_calls = []
        second_result.response = "Final after skill-first retry"
        second_result.provider = "openai"
        second_result.get_response = AsyncMock(
            return_value="Final after skill-first retry"
        )

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result, second_result)
        )

        visitor = MagicMock()
        visitor.utterance = "Search internal pageindex documents about jvagent."
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(return_value=[])
        tool_executor.activated_skills = set()

        task_handle = MagicMock()
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        discovered_skills = {
            "pageindex_search": {
                "description": "Search internal knowledge base documents.",
                "scope_hint": "internal retrieval",
                "metadata": {"tags": ["retrieval", "pageindex"]},
                "tool_files": [],
                "requires_actions": [],
            }
        }

        (
            response,
            termination_reason,
            stuck_corrections,
            result_attributions,
        ) = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=discovered_skills,
        )

        assert response == "Final after skill-first retry"
        assert termination_reason == "completed"
        assert stuck_corrections == 0
        assert action._call_model.await_count == 2

    @pytest.mark.asyncio
    async def test_run_agentic_loop_single_call_for_conversational_utterance(self):
        """Smalltalk / meta questions skip skill-first nudge (one model call)."""
        action = _make_thinking_action(max_iterations=3, skill_first_retry_limit=1)

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = []
        first_result.response = "I can help with code review, search, and more."
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(
            return_value="I can help with code review, search, and more."
        )

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result)
        )

        visitor = MagicMock()
        visitor.utterance = "What can you do?"
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(return_value=[])
        tool_executor.activated_skills = set()

        task_handle = MagicMock()
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        discovered_skills = {
            "code_review": {
                "description": "Review code for quality, security, and correctness.",
                "metadata": {"tags": ["code", "review"]},
                "tool_files": [],
                "requires_actions": [],
            }
        }

        (
            response,
            termination_reason,
            stuck_corrections,
            result_attributions,
        ) = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=discovered_skills,
        )

        assert response == "I can help with code review, search, and more."
        assert termination_reason == "completed"
        assert action._call_model.await_count == 1

    @pytest.mark.asyncio
    async def test_run_agentic_loop_skips_skill_first_when_prioritize_disabled(self):
        action = _make_thinking_action(
            max_iterations=3,
            skill_first_retry_limit=1,
            prioritize_skills_first=False,
        )

        first_result = MagicMock()
        first_result.thinking_content = None
        first_result.thinking_tokens = 0
        first_result.tool_calls = []
        first_result.response = "Direct answer"
        first_result.provider = "openai"
        first_result.get_response = AsyncMock(return_value="Direct answer")

        action._call_model = AsyncMock(
            side_effect=_agentic_call_model_side_effect(first_result)
        )

        visitor = MagicMock()
        visitor.utterance = "Search internal pageindex documents about jvagent."
        visitor.conversation = None
        visitor.interaction = None

        tool_executor = MagicMock()
        tool_executor.get_tools_list = MagicMock(return_value=[])
        tool_executor.dispatch = AsyncMock(return_value=[])
        tool_executor.activated_skills = set()

        task_handle = MagicMock()
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_handle.current_step = MagicMock(return_value=None)
        task_handle.has_pending_steps = MagicMock(return_value=False)
        task_handle.list_steps = MagicMock(return_value=[])
        task_handle.format_plan = MagicMock(return_value="")
        task_handle.pending_steps = MagicMock(return_value=[])
        task_handle.to_checklist = MagicMock(return_value=[])

        discovered_skills = {
            "pageindex_search": {
                "description": "Search internal knowledge base documents.",
                "scope_hint": "internal retrieval",
                "metadata": {"tags": ["retrieval", "pageindex"]},
                "tool_files": [],
                "requires_actions": [],
            }
        }

        (
            response,
            termination_reason,
            _,
            _,
        ) = await action._run_agentic_loop(
            visitor=visitor,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=discovered_skills,
        )

        assert response == "Direct answer"
        assert termination_reason == "completed"
        assert action._call_model.await_count == 1

    def test_skill_first_retry_fires_when_skills_available_and_none_activated(self):
        """Skill-first retry fires when utterance matches catalog and nudges are enabled."""
        sa = SkillAction()
        cfg = SkillRunConfig(
            prioritize_skills_first=True,
            skill_first_retry_limit=1,
            skill_first_retry_min_relevance=0.25,
            plan_first=False,
            final_review=False,
            enable_checkpoints=False,
            enable_evidence_log=False,
        )
        discovered_skills = {
            "code_review": {
                "description": "Review code for quality, security, and correctness.",
                "metadata": {"tags": ["code", "review"]},
                "tool_files": [],
                "requires_actions": [],
            },
        }
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        utterance = "review this code for security issues"
        candidate = "Here is a direct answer without activating a skill yet."

        assert (
            sa._should_retry_for_skill_first(
                cfg=cfg,
                discovered_skills=discovered_skills,
                tool_executor=tool_executor,
                utterance=utterance,
                retries=0,
                candidate_response=candidate,
                tools_ever_called=None,
                nontrivial_tools_called=None,
            )
            is True
        )

        # Does not fire when skills already activated
        tool_executor.activated_skills = {"code_review"}
        assert (
            sa._should_retry_for_skill_first(
                cfg=cfg,
                discovered_skills=discovered_skills,
                tool_executor=tool_executor,
                utterance=utterance,
                retries=0,
                candidate_response=candidate,
                tools_ever_called=None,
                nontrivial_tools_called=None,
            )
            is False
        )

        # Does not fire when no skills configured
        tool_executor.activated_skills = set()
        assert (
            sa._should_retry_for_skill_first(
                cfg=cfg,
                discovered_skills=None,
                tool_executor=tool_executor,
                utterance=utterance,
                retries=0,
                candidate_response=candidate,
                tools_ever_called=None,
                nontrivial_tools_called=None,
            )
            is False
        )

        # Does not fire when retry limit exceeded
        assert (
            sa._should_retry_for_skill_first(
                cfg=cfg,
                discovered_skills=discovered_skills,
                tool_executor=tool_executor,
                utterance=utterance,
                retries=1,
                candidate_response=candidate,
                tools_ever_called=None,
                nontrivial_tools_called=None,
            )
            is False
        )

    def test_search_skills_returns_metadata_driven_matches(self):
        catalog = SkillCatalog(
            {
                "research": {
                    "description": "Investigate topics and synthesize findings.",
                    "scope_hint": "analysis and synthesis",
                    "metadata": {"tags": ["research", "analysis"]},
                    "tool_files": [],
                    "requires_actions": [],
                },
                "web_search": {
                    "description": "Search the public web for supplemental information.",
                    "scope_hint": "external search",
                    "metadata": {"tags": ["search", "retrieval"]},
                    "tool_files": ["search.py"],
                    "requires_actions": [],
                },
                "pageindex_search": {
                    "description": "Search internal knowledge base documents first.",
                    "scope_hint": "internal retrieval",
                    "metadata": {"tags": ["retrieval", "pageindex"]},
                    "tool_files": ["search.py"],
                    "requires_actions": [],
                },
            }
        )

        result = catalog.search(
            query="Hello what is jvagent and explain the relationship between Eldon Marks, V75 and jvagent",
            top_k=3,
        )

        lines = result.splitlines()
        assert lines[0].startswith("Skill matches for")


# --- Helpers ---


def _make_thinking_action(**kwargs):
    """Create a test harness backed by SkillAction + SkillRunConfig.

    Returns a SimpleNamespace that exposes the same surface as the old mock
    helper, but wires all logic directly to SkillAction and SkillRunConfig
    without any backward-compat shims.
    """
    # Map legacy thinking_budget_tokens → reasoning_budget_tokens
    reasoning_budget = kwargs.get("reasoning_budget_tokens", 0) or kwargs.get(
        "thinking_budget_tokens", 0
    )

    run_cfg = SkillRunConfig(
        max_iterations=kwargs.get("max_iterations", 25),
        max_duration_seconds=kwargs.get("max_duration_seconds", 300.0),
        reasoning_budget_tokens=reasoning_budget,
        model=kwargs.get("model", "claude-sonnet-4-20250514"),
        model_temperature=kwargs.get("model_temperature", 0.3),
        model_max_tokens=kwargs.get("model_max_tokens", 8192),
        model_action_type=kwargs.get(
            "model_action_type", "AnthropicLanguageModelAction"
        ),
        skills=kwargs.get("skills", None),
        denied_skills=kwargs.get("denied_skills", []),
        skills_source=kwargs.get("skills_source", "both"),
        tool_servers=kwargs.get("tool_servers", []),
        allow_local_tools=kwargs.get("allow_local_tools", False),
        stream_thinking=kwargs.get("stream_thinking", True),
        stream_reasoning=kwargs.get("stream_reasoning", True),
        reasoning_enabled=kwargs.get("reasoning_enabled", None),
        reasoning_extra=kwargs.get("reasoning_extra", None),
        reasoning_effort=kwargs.get("reasoning_effort", None),
        mirror_assistant_stream_as_thoughts=kwargs.get(
            "mirror_assistant_stream_as_thoughts", None
        ),
        stream_tool_progress=kwargs.get("stream_tool_progress", True),
        commit_intermediate_messages=kwargs.get("commit_intermediate_messages", True),
        relay_thoughts_to_channels=kwargs.get("relay_thoughts_to_channels", False),
        max_full_tool_results=kwargs.get("max_full_tool_results", 10),
        max_tool_result_tokens=kwargs.get("max_tool_result_tokens", 400),
        tool_result_truncation_chars=kwargs.get("tool_result_truncation_chars", 500),
        history_limit=kwargs.get("history_limit", 5),
        call_timeout_seconds=kwargs.get("call_timeout_seconds", 60.0),
        strict_grounding=kwargs.get("strict_grounding", True),
        plan_first=kwargs.get("plan_first", False),
        enable_skill_helper_tools=kwargs.get("enable_skill_helper_tools", True),
        max_skill_activations=kwargs.get("max_skill_activations", 5),
        stuck_detection_window=kwargs.get("stuck_detection_window", 3),
        max_midcourse_corrections=kwargs.get("max_midcourse_corrections", 2),
        final_review=kwargs.get("final_review", False),
        prioritize_skills_first=kwargs.get("prioritize_skills_first", True),
        skill_first_retry_limit=kwargs.get("skill_first_retry_limit", 1),
        skill_first_retry_min_relevance=kwargs.get(
            "skill_first_retry_min_relevance", 0.25
        ),
        conversational_skip_patterns=kwargs.get("conversational_skip_patterns", []),
        skill_first_conversational_heuristic=kwargs.get(
            "skill_first_conversational_heuristic", True
        ),
        conversational_short_utterance_max_chars=kwargs.get(
            "conversational_short_utterance_max_chars", 60
        ),
        conversational_short_utterance_max_tokens=kwargs.get(
            "conversational_short_utterance_max_tokens", 8
        ),
        conversational_heuristic_max_relevance=kwargs.get(
            "conversational_heuristic_max_relevance", 3.0
        ),
        conversational_min_response_chars=kwargs.get(
            "conversational_min_response_chars", 20
        ),
        meta_intent_skip_nudge=kwargs.get("meta_intent_skip_nudge", True),
        meta_intent_patterns=kwargs.get("meta_intent_patterns", []),
        degenerate_response_max_chars=kwargs.get("degenerate_response_max_chars", 25),
        best_candidate_shrink_ratio=kwargs.get("best_candidate_shrink_ratio", 0.4),
        task_nudge_retry_limit=kwargs.get("task_nudge_retry_limit", 2),
        enable_checkpoints=False,
        enable_evidence_log=False,
    )

    action = SimpleNamespace()
    action._run_cfg = run_cfg

    # Expose attributes tests read directly
    action.max_iterations = run_cfg.max_iterations
    action.model_action_type = run_cfg.model_action_type
    action.stream_thinking = run_cfg.stream_thinking
    action.stream_reasoning = run_cfg.stream_reasoning
    action.relay_thoughts_to_channels = run_cfg.relay_thoughts_to_channels
    action.commit_intermediate_messages = run_cfg.commit_intermediate_messages
    action.max_full_tool_results = run_cfg.max_full_tool_results
    action.max_tool_result_tokens = run_cfg.max_tool_result_tokens
    action.tool_result_truncation_chars = run_cfg.tool_result_truncation_chars
    action.history_limit = run_cfg.history_limit

    # Placeholder for tests that set action._call_model = AsyncMock(...)
    action._call_model = None
    # Placeholder for thought / user publish mocks
    action.publish_thought = None
    action.publish = None

    # Wire SkillAction helpers
    action._build_reasoning_model_config = lambda: SkillAction._build_reasoning_cfg(
        run_cfg
    )
    action._maybe_truncate_messages = lambda msgs: ContextCompactor(
        CompactorConfig(
            max_full_tool_results=run_cfg.max_full_tool_results,
            max_tool_result_tokens=run_cfg.max_tool_result_tokens,
            tool_result_truncation_chars=run_cfg.tool_result_truncation_chars,
        )
    ).compact(msgs, evidence_log=EvidenceLog())
    action._build_assistant_content = lambda mr: LoopContext.build_assistant_content(mr)
    action._convert_messages_for_provider = (
        lambda messages, provider: LoopContext.convert_for_provider(messages, provider)
    )
    action._parse_tool_arguments = lambda args: LoopContext.parse_tool_arguments(args)
    action._tool_call_signature = lambda tool_calls: StuckDetector._build_signature(
        tool_calls
    )
    action._search_skills = lambda discovered_skills, query, top_k=5: SkillCatalog(
        discovered_skills
    ).search(query, top_k=top_k)
    action._should_prefer_best_over_candidate = (
        lambda c, b: SkillAction()._should_prefer_best_over_candidate(run_cfg, c, b)
    )
    action._is_degenerate_response = lambda r, **k: SkillAction._is_degenerate_response(
        r, **k
    )
    action._should_retry_for_skill_first = lambda ds, te, u, r, cr=None, tools_ever_called=None, nontrivial_tools_called=None: SkillAction()._should_retry_for_skill_first(
        cfg=run_cfg,
        discovered_skills=ds,
        tool_executor=te,
        utterance=u,
        retries=r,
        candidate_response=cr,
        tools_ever_called=tools_ever_called,
        nontrivial_tools_called=nontrivial_tools_called,
    )
    action._should_nudge_to_execute = lambda *, candidate_response, tool_executor, retries: SkillAction()._should_nudge_to_execute(
        cfg=run_cfg,
        candidate_response=candidate_response,
        tool_executor=tool_executor,
        retries=retries,
    )
    action._should_nudge_for_followthrough = lambda *, utterance, tool_executor, retries, nontrivial_tools_called: SkillAction()._should_nudge_for_followthrough(
        cfg=run_cfg,
        utterance=utterance,
        tool_executor=tool_executor,
        retries=retries,
        nontrivial_tools_called=nontrivial_tools_called,
    )

    async def _run_agentic_loop(
        visitor, tool_executor, task_handle, discovered_skills=None
    ):
        """Bridge to SkillAction._run_loop with publish routing."""
        sa = SkillAction()
        # If test set action._call_model, bridge it to SkillAction's _call_model signature
        if callable(action._call_model):
            bridged = action._call_model
            sa._call_model = bridged  # type: ignore[method-assign]

        async def _publish_cb(
            content,
            *,
            category,
            thought_type,
            segment_id,
            streaming_complete,
            relay_to_adapters,
        ):
            if category == "thought" and action.publish_thought:
                await action.publish_thought(
                    visitor=visitor,
                    content=content,
                    thought_type=thought_type or "reasoning",
                    segment_id=segment_id,
                    streaming_complete=streaming_complete,
                    stream=True,
                    relay_to_adapters=relay_to_adapters,
                )
            elif (
                category == "user"
                and action.publish
                and run_cfg.commit_intermediate_messages
            ):
                await action.publish(
                    visitor=visitor,
                    content=content,
                    streaming_complete=streaming_complete,
                )

        ctx = SkillRunContext(
            utterance=getattr(visitor, "utterance", "") or "",
            conversation=getattr(visitor, "conversation", None),
            model_action=MagicMock(),
            task_store=None,
            config=run_cfg,
            publish_callback=_publish_cb,
        )
        result = await sa._run_loop(
            ctx=ctx,
            tool_executor=tool_executor,
            task_handle=task_handle,
            discovered_skills=discovered_skills or {},
            system_prompt="",
            skill_index_section=None,
        )
        return (
            result.final_response,
            result.termination_reason,
            result.stuck_corrections,
            result.result_attributions,
        )

    action._run_agentic_loop = _run_agentic_loop

    async def _healthcheck():
        return await SkillInteractAction.healthcheck(action)

    action.healthcheck = _healthcheck
    action.get_class_name = MagicMock(return_value="SkillInteractAction")

    return action
