"""Shared fixtures for reasoning helm tests.

Provides a mocked ``EngineContext`` plus helpers to construct deterministic
``ModelActionResult`` sequences and stub tool registries. These fixtures bypass
the real ``assemble_engine_tools`` path so unit tests can exercise the engine
state machine and walker-revisit mechanics without booting an agent graph.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.reasoning.config import EngineConfig
from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.action.model.language.base import ModelActionResult
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_registry import ToolRegistry


def make_tool_call(
    name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    call_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a single OpenAI-style tool_call dict."""
    return {
        "id": call_id or f"call_{name}_{id(arguments)}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}),
        },
    }


def make_lm_result(
    *,
    response: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> ModelActionResult:
    return ModelActionResult(
        response=response,
        tool_calls=tool_calls,
        model="test-model",
        provider="test",
        finish_reason="tool_calls" if tool_calls else "stop",
    )


class ScriptedModelAction:
    """Drop-in mock for LanguageModelAction.

    Returns the next scripted ``ModelActionResult`` per ``query_messages`` call.
    Records call count + every messages payload for assertions.
    """

    def __init__(self, results: List[ModelActionResult]) -> None:
        self._results = list(results)
        self.calls: List[Dict[str, Any]] = []

    async def query_messages(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ModelActionResult:
        self.calls.append(
            {"messages": list(messages), "tools": tools, "kwargs": dict(kwargs)}
        )
        if not self._results:
            raise AssertionError(
                "ScriptedModelAction exhausted; engine called more times than scripted"
            )
        return self._results.pop(0)


def _make_stub_tool(
    name: str,
    *,
    return_content: str = "ok",
    is_error: bool = False,
    side_effect: Optional[Callable[..., Any]] = None,
) -> Tool:
    """Build a minimal Tool whose execute returns canned content."""

    async def _execute(**kwargs: Any) -> str:
        if side_effect is not None:
            res = side_effect(**kwargs)
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, str):
                return res
        if is_error:
            raise RuntimeError(return_content)
        return return_content

    return Tool(
        name=name,
        description=f"stub tool {name}",
        parameters_schema={"type": "object", "properties": {}},
        execute=_execute,
    )


@pytest.fixture
def stub_tool_factory():
    """Factory fixture for cheap test tools."""
    return _make_stub_tool


@pytest.fixture
def stub_registry(stub_tool_factory):
    """A ToolRegistry seeded with a couple of stub tools (echo + fail)."""
    reg = ToolRegistry()
    reg.register(stub_tool_factory("echo", return_content="echo-ok"), prefix="harness")
    reg.register(
        stub_tool_factory("fail", return_content="boom", is_error=True),
        prefix="harness",
    )
    return reg


@pytest.fixture
def engine_config() -> EngineConfig:
    """Default-ish EngineConfig with tight budgets for fast tests."""
    return EngineConfig(
        model="test-model",
        model_temperature=0.0,
        model_max_tokens=1024,
        max_iterations=5,
        max_duration_seconds=10.0,
        max_concurrent_tools=2,
        tool_call_timeout=2.0,
        sanitize_tool_errors=True,
        stuck_detection_window=3,
        stuck_intent_jaccard_threshold=0.65,
        stuck_primary_tool_repeat=3,
        stuck_min_iterations=2,
        plan_first=False,
        max_task_plan_steps=10,
        skills=None,
        denied_skills=[],
        skills_source="both",
        response_mode="publish",
        stream_internal_progress=False,
        enable_skill_helper_tools=False,
        enable_artifact_tools=False,
        enable_capability_search=False,
        preload_user_memory=False,
        user_memory_max_chars=0,
        auto_track_tasks=False,
        skill_index_inline_max_skills=0,
        history_limit=0,
        degenerate_response_max_chars=25,
    )


@pytest.fixture
def mock_persona():
    p = MagicMock()
    p.persona_name = "TestAgent"
    p.persona_description = "A test agent."
    p.enabled = True
    return p


@pytest.fixture
def mock_visitor():
    v = MagicMock()
    v._skill_state = {}
    v.utterance = "test utterance"
    v.session_id = "sess_test"
    v.channel = "default"
    v.stream = False
    v.user_id = "u_test"
    v.response_bus = MagicMock()
    v.tasks = None  # disabled — auto_track_tasks=False
    v.prepend = AsyncMock()
    v.unrecord_action_execution = AsyncMock()
    return v


@pytest.fixture
def mock_conversation():
    c = MagicMock()
    c.get_interaction_history = AsyncMock(return_value=[])
    return c


@pytest.fixture
def mock_interaction():
    i = MagicMock()
    i.id = "int_test_1"
    i.response = ""
    i.save = AsyncMock()
    i.set_to_executed = MagicMock()
    return i


@pytest.fixture
def mock_agent():
    a = MagicMock()
    a.id = "agent_test"
    a.get_actions_manager = AsyncMock(return_value=None)
    return a


@pytest.fixture
def engine_ctx(
    engine_config,
    mock_persona,
    mock_visitor,
    mock_conversation,
    mock_interaction,
    mock_agent,
):
    """A fully wired EngineContext with mocks for graph dependencies."""
    return EngineContext(
        utterance=mock_visitor.utterance,
        conversation=mock_conversation,
        interaction=mock_interaction,
        agent=mock_agent,
        model_action=ScriptedModelAction([]),  # will be overridden per test
        config=engine_config,
        response_bus=mock_visitor.response_bus,
        session_id=mock_visitor.session_id,
        channel=mock_visitor.channel,
        stream=mock_visitor.stream,
        user_id=mock_visitor.user_id,
        persona=mock_persona,
        action=MagicMock(),
        visitor=mock_visitor,
        preloaded_skills=[],
        publish_callback=None,
    )


@pytest.fixture
def patch_assemble_engine_tools(monkeypatch, stub_registry):
    """Patch ``assemble_engine_tools`` so engine.initialize() uses a stub registry.

    Returns the stub registry so tests can inspect/extend it.
    """

    async def _stub_assemble(ctx) -> ToolRegistry:
        return stub_registry

    monkeypatch.setattr(
        "jvagent.action.helm.reasoning.engine.assemble_engine_tools",
        _stub_assemble,
    )
    return stub_registry
