"""C-1 skeleton tests for ``ReasoningHelm``.

These tests verify the placeholder behavior so the package is exercisable
through Bridge end-to-end before the engine is duplicated at C-2. They are
intentionally light — engine-level testing arrives with C-2.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jvagent.action.helm.contracts import EMIT
from jvagent.action.helm.reasoning import ReasoningHelm

pytestmark = pytest.mark.asyncio


async def test_reasoning_helm_instantiates_with_expected_defaults():
    helm = ReasoningHelm()
    assert helm.latency_class == "deliberate"
    assert helm.can_emit_directly is True
    assert helm.can_interrupt is False
    assert helm.model == "claude-sonnet-4-20250514"
    assert helm.model_action_type == "AnthropicLanguageModelAction"


async def test_reasoning_helm_step_returns_emit_finalize_at_c1():
    helm = ReasoningHelm()
    visitor = MagicMock()
    visitor.utterance = "hello world"
    bridge_state = MagicMock()
    bridge_state.helm_states = {}

    result = await helm.step(visitor, bridge_state)

    assert isinstance(result, EMIT)
    assert result.finalize is True
    assert "hello world" in result.text


async def test_reasoning_helm_step_handles_empty_utterance():
    helm = ReasoningHelm()
    visitor = MagicMock()
    visitor.utterance = None
    bridge_state = MagicMock()
    bridge_state.helm_states = {}

    result = await helm.step(visitor, bridge_state)

    assert isinstance(result, EMIT)
    assert "empty utterance" in result.text


async def test_reasoning_helm_name_matches_class_name():
    helm = ReasoningHelm()
    assert helm.helm_name() == "ReasoningHelm"
