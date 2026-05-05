"""Tests for SkillAction core, contracts, checkpoint/recovery, compactor, evidence log."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.skill.context_compactor import CompactorConfig, ContextCompactor
from jvagent.action.skill.loop_checkpoint import CheckpointStore, LoopCheckpoint
from jvagent.action.skill.recovery_policy import (
    FailureRecord,
    RecoveryPolicy,
    RetryDecision,
)
from jvagent.action.skill.skill_action import SkillAction
from jvagent.action.skill.skill_action_contracts import (
    LoopPhase,
    SkillRunConfig,
    SkillRunContext,
    TerminationReason,
)
from jvagent.memory.evidence_log import EvidenceEntry, EvidenceLog
from jvagent.memory.task_store import TaskStore, TaskHandle, Step, Task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conversation(context: Optional[Dict] = None) -> MagicMock:
    conv = MagicMock()
    conv.context = context if context is not None else {}
    conv.tasks = []
    conv.save = AsyncMock()
    return conv


def _make_task_handle() -> MagicMock:
    th = MagicMock()
    th.task_id = "test-task-001"
    th.add_event = AsyncMock(return_value=True)
    th.update = AsyncMock(return_value=True)
    th.list_steps = MagicMock(return_value=[])
    th.complete = AsyncMock(return_value=True)
    th.fail = AsyncMock(return_value=True)
    return th


def _make_task_store() -> MagicMock:
    store = MagicMock()
    store.track = MagicMock(return_value=_tracking_ctx())
    return store


def _tracking_ctx():
    """Returns an async context manager yielding a task handle."""
    ctx = MagicMock()
    handle = _make_task_handle()
    ctx.__aenter__ = AsyncMock(return_value=handle)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_model_result(
    *, response: str = "", tool_calls: Optional[List] = None
) -> MagicMock:
    mr = MagicMock()
    mr.response = response
    mr.tool_calls = tool_calls or []
    mr.thinking_tokens = 0
    mr.metrics = {}
    mr.thinking_content = ""
    mr.get_response = AsyncMock(return_value=response)
    mr.iter_thinking = AsyncMock(return_value=_aiter([]))
    mr.iter_stream = AsyncMock(return_value=_aiter([]))
    return mr


async def _aiter(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# SkillRunConfig defaults
# ---------------------------------------------------------------------------


class TestSkillRunConfig:
    def test_defaults_sane(self):
        cfg = SkillRunConfig()
        assert cfg.max_iterations == 25
        assert cfg.max_duration_seconds == 300.0
        assert cfg.strict_grounding is True
        assert cfg.enable_checkpoints is True
        assert cfg.enable_evidence_log is True

    def test_custom_values_round_trip(self):
        cfg = SkillRunConfig(max_iterations=10, model="gpt-4o", strict_grounding=False)
        assert cfg.max_iterations == 10
        assert cfg.model == "gpt-4o"
        assert cfg.strict_grounding is False


# ---------------------------------------------------------------------------
# TerminationReason / LoopPhase values
# ---------------------------------------------------------------------------


class TestEnums:
    def test_termination_reason_values(self):
        assert TerminationReason.COMPLETED.value == "completed"
        assert TerminationReason.ITER_CAP.value == "max_iterations"
        assert TerminationReason.TIME_CAP.value == "timed_out"
        assert TerminationReason.ERROR.value == "failed"
        assert TerminationReason.STUCK.value == "stuck_forced"

    def test_loop_phase_values(self):
        assert LoopPhase.INIT.value == "init"
        assert LoopPhase.TOOL_DISPATCH.value == "tool_dispatch"
        assert LoopPhase.TERMINATE.value == "terminate"


# ---------------------------------------------------------------------------
# SkillAction static / pure helpers
# ---------------------------------------------------------------------------


class TestSkillActionHelpers:
    engine = SkillAction()

    def test_reorder_task_calls_create_then_read_skill_then_rest(self):
        read_file = {
            "id": "1",
            "function": {
                "name": "mcp_filesystem__read",
                "arguments": json.dumps({"path": "/a"}),
            },
        }
        create = {
            "id": "2",
            "function": {
                "name": "task_tracker",
                "arguments": json.dumps(
                    {"action": "create", "steps": ["Read file", "Summarize"]}
                ),
            },
        }
        o = SkillAction._reorder_task_calls_dependency_first([read_file, create])
        assert o[0] is create
        assert o[1] is read_file
        # read_skill runs before that skill's tools in the same batch
        rs = {
            "id": "r",
            "function": {
                "name": "read_skill",
                "arguments": json.dumps({"skill_name": "answer"}),
            },
        }
        st = {
            "id": "s",
            "function": {
                "name": "answer__search",
                "arguments": json.dumps({"query": "V75"}),
            },
        }
        o2 = SkillAction._reorder_task_calls_dependency_first([st, rs, read_file])
        assert o2[0] is rs
        assert o2[1] is st
        assert o2[2] is read_file

    def test_apply_plan_first_tool_gate_allows_create_batch(self):
        create = {
            "id": "1",
            "function": {
                "name": "task_tracker",
                "arguments": json.dumps({"action": "create", "steps": ["A"]}),
            },
        }
        read = {
            "id": "2",
            "function": {"name": "mcp_x", "arguments": "{}"},
        }
        d, syn, blocked = SkillAction._apply_plan_first_tool_gate(
            [read, create],
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=False,
            activated_skill_names=set(),
        )
        assert d == [read, create]
        assert syn == []
        assert not blocked

    def test_apply_plan_first_tool_gate_blocks_substantive_without_plan(self):
        d, syn, blocked = SkillAction._apply_plan_first_tool_gate(
            [
                {
                    "id": "1",
                    "function": {
                        "name": "mcp_x",
                        "arguments": "{}",
                    },
                }
            ],
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=False,
            activated_skill_names=set(),
        )
        assert d == []
        assert len(syn) == 1
        assert "mcp_x" in blocked
        assert (
            "create" in syn[0]["content"].lower() or "task_tracker" in syn[0]["content"]
        )

    def test_apply_plan_first_tool_gate_allows_helpers(self):
        d, syn, blocked = SkillAction._apply_plan_first_tool_gate(
            [
                {
                    "id": "1",
                    "function": {
                        "name": "read_skill",
                        "arguments": json.dumps({"skill_name": "x"}),
                    },
                },
                {
                    "id": "2",
                    "function": {
                        "name": "mcp_x",
                        "arguments": "{}",
                    },
                },
            ],
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=False,
            activated_skill_names=set(),
        )
        assert len(d) == 1
        assert d[0]["function"]["name"] == "read_skill"
        assert len(syn) == 1
        assert "mcp_x" in blocked

    def test_apply_plan_first_skipped_for_meta_utterance(self):
        d, syn, _ = SkillAction._apply_plan_first_tool_gate(
            [
                {
                    "id": "1",
                    "function": {
                        "name": "mcp_x",
                        "arguments": "{}",
                    },
                }
            ],
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=True,
            activated_skill_names=set(),
        )
        assert len(d) == 1
        assert syn == []

    def test_apply_plan_first_allows_skill_tools_after_in_batch_read_skill(self):
        """Same turn: read_skill(answer) + answer__search must not be blocked (regression)."""
        batch = [
            {
                "id": "1",
                "function": {
                    "name": "read_skill",
                    "arguments": json.dumps({"skill_name": "answer"}),
                },
            },
            {
                "id": "2",
                "function": {
                    "name": "answer__search",
                    "arguments": json.dumps({"query": "V75 Inc"}),
                },
            },
        ]
        d, syn, blocked = SkillAction._apply_plan_first_tool_gate(
            batch,
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=False,
            activated_skill_names=set(),
        )
        assert len(d) == 2
        assert not syn
        assert not blocked

    def test_apply_plan_first_allows_skill_tools_when_activated(self):
        d, syn, blocked = SkillAction._apply_plan_first_tool_gate(
            [
                {
                    "id": "2",
                    "function": {
                        "name": "answer__search",
                        "arguments": json.dumps({"query": "x"}),
                    },
                }
            ],
            plan_first=True,
            has_task_plan=False,
            is_meta_utterance=False,
            activated_skill_names={"answer"},
        )
        assert len(d) == 1
        assert not syn
        assert not blocked

    def test_merge_tool_dispatch_with_synthetic(self):
        t1 = {"id": "a", "function": {"name": "read_skill", "arguments": "{}"}}
        t2 = {"id": "b", "function": {"name": "mcp", "arguments": "{}"}}
        dres = [
            {
                "role": "tool",
                "tool_call_id": "a",
                "content": "ok-skill",
            }
        ]
        sres = [
            {
                "role": "tool",
                "tool_call_id": "b",
                "content": "Error: blocked",
            }
        ]
        out = SkillAction._merge_tool_dispatch_with_synthetic([t1, t2], dres, sres)
        assert len(out) == 2
        assert out[0]["content"] == "ok-skill"
        assert "blocked" in out[1]["content"]

    def test_is_degenerate_response(self):
        assert SkillAction._is_degenerate_response("")
        assert SkillAction._is_degenerate_response("ok")
        assert SkillAction._is_degenerate_response("done")
        assert not SkillAction._is_degenerate_response(
            "Here is a full response to your question."
        )

    def test_update_best_candidate_keeps_longer(self):
        assert (
            SkillAction._update_best_candidate("short", "much longer answer here")
            == "much longer answer here"
        )
        assert (
            SkillAction._update_best_candidate("already long answer here", "short")
            == "already long answer here"
        )

    def test_update_best_candidate_empty(self):
        assert SkillAction._update_best_candidate(None, "first") == "first"
        assert SkillAction._update_best_candidate("existing", "") == "existing"

    def test_should_prefer_best_over_candidate(self):
        cfg = SkillRunConfig()
        engine = SkillAction()
        assert engine._should_prefer_best_over_candidate(cfg, "ok", "longer response")
        assert not engine._should_prefer_best_over_candidate(
            cfg, "Here is a sufficient answer", "short"
        )

    def test_clean_tool_name_namespaced(self):
        assert SkillAction._clean_tool_name("myskill__search") == "search"
        assert SkillAction._clean_tool_name("search") == "search"

    def test_extract_tool_intent_query(self):
        import json

        args = json.dumps({"query": "python docs"})
        intent = SkillAction._extract_tool_intent(args)
        assert "python docs" in intent

    def test_resolve_thinking_token_count_from_attribute(self):
        mr = MagicMock()
        mr.thinking_tokens = 500
        assert SkillAction._resolve_thinking_token_count(mr) == 500

    def test_resolve_thinking_token_count_zero(self):
        mr = MagicMock()
        mr.thinking_tokens = 0
        mr.metrics = {}
        mr.thinking_content = ""
        assert SkillAction._resolve_thinking_token_count(mr) == 0

    def test_extract_result_attributions_uuid(self):
        content = "Record id: 550e8400-e29b-41d4-a716-446655440000"
        attrs = SkillAction._extract_result_attributions(content, "call-001")
        types = {a["claim_type"] for a in attrs}
        assert "uuid" in types

    def test_extract_result_attributions_url(self):
        content = "See https://example.com/path?foo=bar for details."
        attrs = SkillAction._extract_result_attributions(content, "call-002")
        assert any(a["claim_type"] == "url" for a in attrs)

    def test_verify_grounding_clean(self):
        attrs = [
            {
                "claim": "https://example.com",
                "source_tool_call_id": "c1",
                "claim_type": "url",
            }
        ]
        response = "See https://example.com for more info."
        result, unattributed = SkillAction._verify_grounding(
            response, attrs, strict=True
        )
        assert "[unverified]" not in result
        assert unattributed == []

    def test_verify_grounding_flags_unattributed(self):
        attrs = [
            {
                "claim": "https://known.com",
                "source_tool_call_id": "c1",
                "claim_type": "url",
            }
        ]
        response = "See https://unknown.org for info."
        result, unattributed = SkillAction._verify_grounding(
            response, attrs, strict=True
        )
        assert len(unattributed) == 1
        assert "[unverified]" in result

    def test_verify_grounding_non_strict(self):
        attrs = [
            {
                "claim": "https://known.com",
                "source_tool_call_id": "c1",
                "claim_type": "url",
            }
        ]
        response = "See https://unknown.org for info."
        result, unattributed = SkillAction._verify_grounding(
            response, attrs, strict=False
        )
        # Non-strict should still detect but NOT annotate
        assert "[unverified]" not in result
        assert len(unattributed) == 1


# ---------------------------------------------------------------------------
# LoopCheckpoint
# ---------------------------------------------------------------------------


class TestLoopCheckpoint:
    def test_round_trip(self):
        ckpt = LoopCheckpoint(
            iteration=3,
            phase="model_call",
            elapsed_seconds=12.5,
            pending_tool_names=["search", "read"],
            termination_reason_candidate="completed",
        )
        d = ckpt.to_dict()
        restored = LoopCheckpoint.from_dict(d)
        assert restored.iteration == 3
        assert restored.phase == "model_call"
        assert restored.pending_tool_names == ["search", "read"]

    @pytest.mark.asyncio
    async def test_checkpoint_store_save_and_load(self):
        conv = _make_conversation()
        store = CheckpointStore(conv)
        ckpt = LoopCheckpoint(
            iteration=5,
            phase="tool_dispatch",
            elapsed_seconds=30.1,
        )
        await store.save(ckpt)
        loaded = store.load()
        assert loaded is not None
        assert loaded.iteration == 5
        assert loaded.phase == "tool_dispatch"

    @pytest.mark.asyncio
    async def test_checkpoint_store_clear(self):
        conv = _make_conversation()
        store = CheckpointStore(conv)
        ckpt = LoopCheckpoint(iteration=1, phase="init", elapsed_seconds=0.0)
        await store.save(ckpt)
        await store.clear()
        assert store.load() is None

    def test_checkpoint_store_load_none_when_empty(self):
        conv = _make_conversation()
        store = CheckpointStore(conv)
        assert store.load() is None

    def test_checkpoint_store_no_conversation(self):
        store = CheckpointStore(None)
        assert store.load() is None

    @pytest.mark.asyncio
    async def test_checkpoint_store_save_none_conversation(self):
        store = CheckpointStore(None)
        # Should not raise
        ckpt = LoopCheckpoint(iteration=1, phase="init", elapsed_seconds=0.0)
        await store.save(ckpt)


# ---------------------------------------------------------------------------
# RecoveryPolicy
# ---------------------------------------------------------------------------


class TestRecoveryPolicy:
    def test_non_recoverable_returns_terminate(self):
        policy = RecoveryPolicy()
        failure = FailureRecord(
            iteration=1,
            phase="model_call",
            error="permission denied",
            recoverable=False,
        )
        assert policy.decide(failure).action == "terminate"

    def test_recoverable_within_budget_returns_retry(self):
        policy = RecoveryPolicy()
        failure = FailureRecord(
            iteration=1,
            phase="model_call",
            error="temporary server error",
            recoverable=True,
        )
        assert policy.decide(failure).action == "retry"

    def test_budget_exhaustion_returns_terminate(self):
        policy = RecoveryPolicy(phase_retry_budgets={"model_call": 1})
        failure = FailureRecord(
            iteration=1, phase="model_call", error="timeout", recoverable=True
        )
        assert policy.decide(failure).action == "retry"  # first attempt
        assert policy.decide(failure).action == "terminate"  # budget exhausted

    def test_different_iterations_get_own_budgets(self):
        policy = RecoveryPolicy(phase_retry_budgets={"model_call": 1})
        f1 = FailureRecord(
            iteration=1, phase="model_call", error="err", recoverable=True
        )
        f2 = FailureRecord(
            iteration=2, phase="model_call", error="err", recoverable=True
        )
        assert policy.decide(f1).action == "retry"
        assert policy.decide(f1).action == "terminate"
        assert policy.decide(f2).action == "retry"  # iter 2 has fresh budget

    def test_is_recoverable_heuristic(self):
        policy = RecoveryPolicy()
        assert policy.is_recoverable(Exception("temporary timeout"))
        assert not policy.is_recoverable(Exception("permission denied"))
        assert not policy.is_recoverable(Exception("invalid api key"))

    def test_reset(self):
        policy = RecoveryPolicy(phase_retry_budgets={"model_call": 1})
        f = FailureRecord(
            iteration=1, phase="model_call", error="err", recoverable=True
        )
        policy.decide(f)
        policy.reset()
        assert policy.decide(f).action == "retry"  # budget fresh after reset


# ---------------------------------------------------------------------------
# EvidenceLog
# ---------------------------------------------------------------------------


class TestEvidenceLog:
    def test_append_and_len(self):
        log = EvidenceLog()
        log.append(
            iteration=1,
            tool_call_id="c1",
            tool_name="search",
            input_args='{"query": "test"}',
            content="result text",
        )
        assert len(log) == 1

    def test_entry_fields(self):
        log = EvidenceLog()
        entry = log.append(
            iteration=2,
            tool_call_id="c2",
            tool_name="myfact__lookup",
            input_args='{"id": "123"}',
            content="Error: not found",
        )
        assert entry.iteration == 2
        assert entry.tool_name == "myfact__lookup"
        assert entry.is_error is True

    def test_by_tool_call_id(self):
        log = EvidenceLog()
        log.append(
            iteration=1, tool_call_id="a", tool_name="t1", input_args="{}", content="r1"
        )
        log.append(
            iteration=1, tool_call_id="b", tool_name="t2", input_args="{}", content="r2"
        )
        assert log.by_tool_call_id("a").tool_name == "t1"
        assert log.by_tool_call_id("b").tool_name == "t2"
        assert log.by_tool_call_id("z") is None

    def test_for_iteration(self):
        log = EvidenceLog()
        log.append(
            iteration=1, tool_call_id="a", tool_name="t1", input_args="{}", content="r1"
        )
        log.append(
            iteration=2, tool_call_id="b", tool_name="t2", input_args="{}", content="r2"
        )
        log.append(
            iteration=2, tool_call_id="c", tool_name="t3", input_args="{}", content="r3"
        )
        assert len(log.for_iteration(2)) == 2
        assert len(log.for_iteration(1)) == 1

    def test_successful_filters_errors(self):
        log = EvidenceLog()
        log.append(
            iteration=1,
            tool_call_id="a",
            tool_name="t1",
            input_args="{}",
            content="good result",
        )
        log.append(
            iteration=1,
            tool_call_id="b",
            tool_name="t2",
            input_args="{}",
            content="Error: something failed",
        )
        assert len(log.successful()) == 1

    def test_persist_to_and_load_from(self):
        log = EvidenceLog()
        log.append(
            iteration=1,
            tool_call_id="c1",
            tool_name="search",
            input_args='{"q":"x"}',
            content="result",
        )
        conv = _make_conversation()
        log.persist_to(conv)
        restored = EvidenceLog.load_from(conv)
        assert len(restored) == 1
        entry = list(restored)[0]
        assert entry.tool_name == "search"
        assert entry.iteration == 1

    def test_persist_appends_to_existing(self):
        conv = _make_conversation()
        log1 = EvidenceLog()
        log1.append(
            iteration=1,
            tool_call_id="c1",
            tool_name="t1",
            input_args="{}",
            content="r1",
        )
        log1.persist_to(conv)

        log2 = EvidenceLog()
        log2.append(
            iteration=2,
            tool_call_id="c2",
            tool_name="t2",
            input_args="{}",
            content="r2",
        )
        log2.persist_to(conv)

        combined = EvidenceLog.load_from(conv)
        assert len(combined) == 2

    def test_input_fingerprint_deterministic(self):
        log = EvidenceLog()
        e1 = log.append(
            iteration=1,
            tool_call_id="x",
            tool_name="t",
            input_args='{"k":"v"}',
            content="",
        )
        log2 = EvidenceLog()
        e2 = log2.append(
            iteration=1,
            tool_call_id="x",
            tool_name="t",
            input_args='{"k":"v"}',
            content="",
        )
        assert e1.input_fingerprint == e2.input_fingerprint


# ---------------------------------------------------------------------------
# ContextCompactor
# ---------------------------------------------------------------------------


def _make_messages(n_tool_results: int) -> List[Dict]:
    msgs: List[Dict] = [{"role": "system", "content": "system"}]
    msgs.append({"role": "user", "content": "initial question"})
    for i in range(n_tool_results):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{i}",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {"role": "tool", "tool_call_id": f"call-{i}", "content": f"result {i}"}
        )
    return msgs


class TestContextCompactor:
    def test_no_compaction_below_threshold(self):
        cfg = CompactorConfig(max_full_tool_results=10)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(5)
        result = compactor.compact(msgs)
        assert len(result) == len(msgs)

    def test_compaction_above_threshold(self):
        cfg = CompactorConfig(max_full_tool_results=3)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(10)
        result = compactor.compact(msgs)
        # Compaction summarises older results in-place; total count stays the same
        # but some content becomes the summarised placeholder
        summarised = [m for m in result if "summarised" in (m.get("content") or "")]
        assert len(summarised) > 0  # some results were summarised

    def test_system_message_always_retained(self):
        cfg = CompactorConfig(max_full_tool_results=2)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(8)
        result = compactor.compact(msgs)
        assert result[0]["role"] == "system"

    def test_last_n_tool_results_full(self):
        cfg = CompactorConfig(max_full_tool_results=3)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(8)
        result = compactor.compact(msgs)
        tool_results = [m for m in result if m.get("role") == "tool"]
        full_results = [
            m for m in tool_results if "summarised" not in (m.get("content") or "")
        ]
        assert len(full_results) == 3

    def test_summarised_messages_include_provenance(self):
        cfg = CompactorConfig(max_full_tool_results=2)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(6)
        log = EvidenceLog()
        for i in range(6):
            log.append(
                iteration=i,
                tool_call_id=f"call-{i}",
                tool_name=f"tool_{i}",
                input_args="{}",
                content=f"result {i}",
            )
        result = compactor.compact(msgs, evidence_log=log)
        summarised = [
            m
            for m in result
            if m.get("role") == "tool" and "summarised" in (m.get("content") or "")
        ]
        # Each summarised message should mention tool name
        for m in summarised:
            assert "tool=" in (m.get("content") or "")

    def test_pairs_never_orphaned(self):
        """An assistant tool-call message and its result must both be kept or summarised."""
        cfg = CompactorConfig(max_full_tool_results=2)
        compactor = ContextCompactor(cfg)
        msgs = _make_messages(8)
        result = compactor.compact(msgs)
        # Verify every assistant message with tool_calls has a corresponding tool result
        for i, msg in enumerate(result):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                call_ids = {tc["id"] for tc in msg["tool_calls"]}
                # Find the matching tool result in remaining messages
                found = any(
                    m.get("role") == "tool" and m.get("tool_call_id") in call_ids
                    for m in result[i + 1 :]
                )
                assert found, f"Orphaned tool call at index {i}: {msg}"

    def test_is_tool_result_openai_format(self):
        msg = {"role": "tool", "tool_call_id": "c1", "content": "result"}
        assert ContextCompactor._is_tool_result(msg)

    def test_is_tool_result_anthropic_format(self):
        msg = {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "r"}],
        }
        assert ContextCompactor._is_tool_result(msg)

    def test_is_tool_result_false_for_user_text(self):
        msg = {"role": "user", "content": "hello"}
        assert not ContextCompactor._is_tool_result(msg)


# ---------------------------------------------------------------------------
# SkillRunContext build
# ---------------------------------------------------------------------------


class TestSkillRunContext:
    def test_context_can_be_built_without_interact(self):
        ctx = SkillRunContext(
            utterance="do something",
            conversation=_make_conversation(),
            model_action=MagicMock(),
            task_store=_make_task_store(),
            config=SkillRunConfig(),
        )
        # All interact-specific fields are optional
        assert ctx.interaction is None
        assert ctx.response_bus is None
        assert ctx.session_id is None

    def test_context_with_publish_callback(self):
        called = []

        async def cb(content, *, category, **kw):
            called.append(content)

        ctx = SkillRunContext(
            utterance="test",
            conversation=_make_conversation(),
            model_action=MagicMock(),
            task_store=_make_task_store(),
            config=SkillRunConfig(),
            publish_callback=cb,
        )
        assert ctx.publish_callback is cb
