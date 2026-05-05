"""Tests for agentic loop lifecycle: skill-first retry, forced termination, stuck detection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.skill_action import SkillAction
from jvagent.action.skill.skill_action_contracts import SkillRunConfig
from jvagent.action.skill.skill_catalog import SkillCatalog
from jvagent.action.skill.stuck_detector import StuckDetector, StuckDetectorConfig

# --- Skill-first retry logic ---


class TestShouldRetryForSkillFirst:
    _GMAIL_SKILL = {
        "gmail": {
            "description": "Send and read messages via Gmail.",
            "metadata": {"tags": ["email", "gmail"]},
            "tool_files": [],
            "requires_actions": [],
        }
    }

    def _make_cfg(self, prioritize=True, retry_limit=1, min_relevance=0.25, **conv_kw):
        return SkillRunConfig(
            prioritize_skills_first=prioritize,
            skill_first_retry_limit=retry_limit,
            skill_first_retry_min_relevance=min_relevance,
            conversational_skip_patterns=conv_kw.get(
                "conversational_skip_patterns", []
            ),
            skill_first_conversational_heuristic=conv_kw.get(
                "skill_first_conversational_heuristic", True
            ),
            conversational_short_utterance_max_chars=conv_kw.get(
                "conversational_short_utterance_max_chars", 60
            ),
            conversational_short_utterance_max_tokens=conv_kw.get(
                "conversational_short_utterance_max_tokens", 8
            ),
            conversational_heuristic_max_relevance=conv_kw.get(
                "conversational_heuristic_max_relevance", 3.0
            ),
            conversational_min_response_chars=conv_kw.get(
                "conversational_min_response_chars", 20
            ),
            meta_intent_skip_nudge=conv_kw.get("meta_intent_skip_nudge", True),
            meta_intent_patterns=conv_kw.get("meta_intent_patterns", []),
            degenerate_response_max_chars=conv_kw.get(
                "degenerate_response_max_chars", 25
            ),
            best_candidate_shrink_ratio=conv_kw.get("best_candidate_shrink_ratio", 0.4),
            plan_first=False,
            final_review=False,
            enable_checkpoints=False,
            enable_evidence_log=False,
        )

    def _should_retry(
        self,
        cfg,
        ds,
        te,
        u,
        r,
        cr=None,
        tools_ever_called=None,
        nontrivial_tools_called=None,
    ):
        return SkillAction()._should_retry_for_skill_first(
            cfg=cfg,
            discovered_skills=ds,
            tool_executor=te,
            utterance=u,
            retries=r,
            candidate_response=cr,
            tools_ever_called=tools_ever_called,
            nontrivial_tools_called=nontrivial_tools_called,
        )

    def test_fires_when_skills_available_none_activated_under_limit(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg, self._GMAIL_SKILL, tool_executor, "send an email using gmail", 0
            )
            is True
        )

    def test_does_not_fire_when_disabled(self):
        cfg = self._make_cfg(prioritize=False)
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg, self._GMAIL_SKILL, tool_executor, "send an email using gmail", 0
            )
            is False
        )

    def test_does_not_fire_when_no_skills(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert self._should_retry(cfg, None, tool_executor, "any", 0) is False

    def test_does_not_fire_when_skills_activated(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = {"gmail"}
        assert (
            self._should_retry(
                cfg, self._GMAIL_SKILL, tool_executor, "send an email using gmail", 0
            )
            is False
        )

    def test_does_not_fire_when_retry_limit_exceeded(self):
        cfg = self._make_cfg(retry_limit=1)
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg, self._GMAIL_SKILL, tool_executor, "send an email using gmail", 1
            )
            is False
        )

    def test_does_not_fire_when_no_tool_executor(self):
        cfg = self._make_cfg()
        assert (
            self._should_retry(
                cfg, self._GMAIL_SKILL, None, "send an email using gmail", 0
            )
            is False
        )

    def test_does_not_fire_when_no_skill_relevance(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "hello there friend",
                0,
                "A warm conversational reply that is long enough.",
            )
            is False
        )

    def test_does_not_fire_for_conversational_meta_with_answer(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "What can you do?",
                0,
                "I can help with email, coding, and research tasks among other things.",
            )
            is False
        )

    def test_does_not_fire_when_utterance_below_relevance_not_conversational(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "what is the capital of France",
                0,
                "Paris is the capital of France.",
            )
            is False
        )

    def test_custom_conversational_skip_pattern_skips_even_when_heuristic_disabled(
        self,
    ):
        """Locale-specific regex in ``conversational_skip_patterns`` (no English defaults)."""
        cfg = self._make_cfg(
            skill_first_conversational_heuristic=False,
            conversational_skip_patterns=[r"^hello\b"],
        )
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "hello please email support about gmail",
                0,
                "I will help you draft an email to support.",
            )
            is False
        )

    def test_custom_pattern_does_not_match_allows_retry_when_relevant(self):
        cfg = self._make_cfg(
            skill_first_conversational_heuristic=False,
            conversational_skip_patterns=[r"^salut\b"],
        )
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "send an email using gmail",
                0,
                "Here is how to send a message with Google email.",
            )
            is True
        )

    def test_does_not_fire_when_any_tool_was_used(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "send an email using gmail",
                0,
                "I'll help with that.",
                tools_ever_called={"list_skills"},
            )
            is False
        )

    def test_does_not_fire_for_meta_what_are_your_skills(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        assert (
            self._should_retry(
                cfg,
                self._GMAIL_SKILL,
                tool_executor,
                "What are your skills?",
                0,
                "You have gmail and other tools.",
            )
            is False
        )

    def test_does_not_fire_when_candidate_names_installed_skill(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        skills = {
            "gmail": {
                "description": "Gmail",
                "metadata": {"tags": []},
                "tool_files": [],
                "requires_actions": [],
            }
        }
        long_ans = "You can use the **gmail** skill to send email. " * 2
        assert len(long_ans) >= 20
        assert (
            self._should_retry(
                cfg,
                skills,
                tool_executor,
                "what should I use for company email",
                0,
                long_ans,
            )
            is False
        )


# --- In-loop task tracker helpers ---


class TestTaskTrackerHandler:
    @staticmethod
    def _make_handler(iteration: int = 1, final_review: bool = False):
        from jvagent.action.skill.skill_action_contracts import SkillRunConfig
        from jvagent.memory.task_store import Task, TaskHandle, TaskStore

        conv = MagicMock()
        conv.tasks = []
        conv.save = AsyncMock()
        store = TaskStore(conv)
        task = Task(id="task_123", title="Test", description="Test", owner_action="SkillAction")
        task_handle = TaskHandle(store, task)
        task_handle.add_event = AsyncMock()
        task_handle.update = AsyncMock()
        task_plan_state: dict = {}
        publish_callback = AsyncMock()
        ctx = SimpleNamespace(
            publish_callback=publish_callback,
            config=SimpleNamespace(stream_tool_progress=True),
        )
        handler = SkillAction()._make_task_tracker_handler(
            ctx=ctx,
            cfg=SkillRunConfig(),
            task_plan_state=task_plan_state,
            task_handle=task_handle,
            iteration_getter=lambda: iteration,
            review_enabled=final_review,
        )
        return handler, task_plan_state, task_handle, publish_callback

    @pytest.mark.asyncio
    async def test_create_read_and_complete_plan(self):
        handler, _, task_handle, publish_callback = self._make_handler()

        created = await handler(
            {"action": "create", "steps": ["Search the web", "Write the report"]}
        )
        assert "Task plan created" in created
        assert task_handle.has_pending_steps()
        assert "1. [in_progress] Search the web" in created

        read = await handler({"action": "read"})
        assert "Current task plan" in read
        assert "2. [pending] Write the report" in read

        completed = await handler({"action": "complete", "step_id": 1})
        assert "Completed step 1" in completed
        # Step IDs are now UUIDs; verify by presence of "Next step is" fragment
        assert "Next step is" in completed
        assert "Write the report" in completed
        published_messages = [call.args[0] for call in publish_callback.await_args_list]
        assert published_messages == [
            "Planning my approach - 2 steps.",
            "Completed: step 1/2: Search the web. Moving to: step 2/2: Write the report.",
        ]

    @pytest.mark.asyncio
    async def test_complete_requires_current_step_order(self):
        handler, _, _, _ = self._make_handler()
        await handler({"action": "create", "steps": ["Search", "Write"]})
        result = await handler({"action": "complete", "step_id": 2})
        assert "steps must be completed in order" in result

    @pytest.mark.asyncio
    async def test_read_without_plan_errors(self):
        handler, _, _, _ = self._make_handler()
        result = await handler({"action": "read"})
        assert "No task plan exists yet" in result

    @pytest.mark.asyncio
    async def test_create_status_mentions_review_when_enabled(self):
        handler, _, _, publish_callback = self._make_handler(final_review=True)

        await handler({"action": "create", "steps": ["Search", "Write"]})

        assert publish_callback.await_args_list[0].args[0] == (
            "Planning my approach - 2 steps + review."
        )

    @pytest.mark.asyncio
    async def test_complete_last_step_emits_finalizing_status(self):
        handler, _, _, publish_callback = self._make_handler()

        await handler({"action": "create", "steps": ["Search"]})
        completed = await handler({"action": "complete", "step_id": 1})

        assert "All tracked steps are now done" in completed
        assert publish_callback.await_args_list[-1].args[0] == (
            "All steps complete, finalizing."
        )

    @pytest.mark.asyncio
    async def test_skip_mid_plan_emits_skipped_moving_next_not_completed_skipped(self):
        handler, _, _, publish_callback = self._make_handler()

        await handler(
            {"action": "create", "steps": ["Step one", "Step two", "Step three"]}
        )
        skipped = await handler(
            {
                "action": "skip",
                "step_id": 1,
                "reason": "cannot do step one in this environment",
            }
        )
        assert "Skipped step 1" in skipped
        published_messages = [call.args[0] for call in publish_callback.await_args_list]
        assert published_messages == [
            "Planning my approach - 3 steps.",
            ("Skipped: step 1/3: Step one. Moving to: step 2/3: Step two."),
        ]

    @pytest.mark.asyncio
    async def test_skip_last_step_emits_some_steps_skipped_finalizing(self):
        handler, _, _, publish_callback = self._make_handler()

        await handler({"action": "create", "steps": ["Only step"]})
        await handler({"action": "skip", "step_id": 1, "reason": "no tool available"})
        assert publish_callback.await_args_list[-1].args[0] == (
            "Some steps skipped, finalizing."
        )


class TestSkillFirstNontrivialToolGating:
    _GMAIL_SKILL = {
        "gmail": {
            "description": "Send and read messages via Gmail.",
            "metadata": {"tags": ["email", "gmail"]},
            "tool_files": [],
            "requires_actions": [],
        }
    }

    def _make_cfg(self) -> SkillRunConfig:
        return SkillRunConfig(
            prioritize_skills_first=True,
            skill_first_retry_limit=1,
            skill_first_retry_min_relevance=0.0,
            conversational_skip_patterns=[],
            skill_first_conversational_heuristic=False,
            conversational_short_utterance_max_chars=60,
            conversational_short_utterance_max_tokens=8,
            conversational_heuristic_max_relevance=3.0,
            conversational_min_response_chars=2000,
            meta_intent_skip_nudge=True,
            meta_intent_patterns=[],
            plan_first=False,
            final_review=False,
            enable_checkpoints=False,
            enable_evidence_log=False,
        )

    def test_helper_only_tools_do_not_block_nudge(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        # The new contract: when nontrivial_tools_called is supplied and empty,
        # prior helper-tool calls (list_skills/skill_search/etc.) do not block.
        result = SkillAction()._should_retry_for_skill_first(
            cfg=cfg,
            discovered_skills=self._GMAIL_SKILL,
            tool_executor=tool_executor,
            utterance="send an email using gmail",
            retries=0,
            candidate_response="I'll help with that.",
            tools_ever_called={"list_skills"},
            nontrivial_tools_called=set(),
        )
        assert result is True

    def test_real_tool_calls_block_nudge(self):
        cfg = self._make_cfg()
        tool_executor = MagicMock()
        tool_executor.activated_skills = set()
        result = SkillAction()._should_retry_for_skill_first(
            cfg=cfg,
            discovered_skills=self._GMAIL_SKILL,
            tool_executor=tool_executor,
            utterance="send an email using gmail",
            retries=0,
            candidate_response="I'll help with that.",
            tools_ever_called={"list_skills", "web_search"},
            nontrivial_tools_called={"web_search"},
        )
        assert result is False


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


# --- Meta-intent detection ---


class TestSkillCatalogMetaIntent:
    def test_is_meta_intent_phrases(self):
        assert SkillCatalog.is_meta_intent("What are your skills?")
        assert SkillCatalog.is_meta_intent("what can you do")
        assert SkillCatalog.is_meta_intent("Who are you?")
        assert not SkillCatalog.is_meta_intent(
            "email someone about the quarterly report"
        )

    def test_is_meta_intent_extra_pattern(self):
        assert not SkillCatalog.is_meta_intent("phoenix mode", extra_patterns=[])
        assert SkillCatalog.is_meta_intent(
            "turn on phoenix mode", extra_patterns=[r"phoenix mode"]
        )


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

    def test_top_relevance_score_matches_search_ranking(self):
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
        score = catalog.top_relevance_score("research and analysis synthesis")
        assert score >= 0.25
        assert catalog.has_relevant_match("research and analysis synthesis", 0.25)
        assert catalog.top_relevance_score("xyzzy unrelated nope") == 0.0
        assert not catalog.has_relevant_match("xyzzy unrelated nope", 0.25)


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
