"""Sanity tests for ``ReasoningHelm`` shape after C-6 wiring.

The C-1 placeholder ``step()`` is replaced at C-6 with the duplicated
cockpit orchestration (Phase 1 router + Phase 2 engine). These tests
cover the bare construction + attribute surface; full engine + routing
behaviour is exercised in ``test_engine_baseline.py`` and the C-7 smoke
harness.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.contracts import YIELD
from jvagent.action.helm.reasoning import ReasoningHelm

pytestmark = pytest.mark.asyncio


async def test_reasoning_helm_instantiates_with_cockpit_defaults():
    helm = ReasoningHelm()
    assert helm.latency_class == "deliberate"
    assert helm.can_emit_directly is True
    # Mirror cockpit defaults so the smoke harness compares apples-to-apples.
    assert helm.model == "claude-sonnet-4-20250514"
    assert helm.model_action_type == "AnthropicLanguageModelAction"
    assert helm.router_model == "gpt-4o-mini"
    assert helm.max_iterations == 25
    assert helm.max_duration_seconds == 300.0
    assert helm.tool_tier == "standard"
    assert helm.conversational_fast_path is True


async def test_reasoning_helm_helm_name_matches_class_name():
    helm = ReasoningHelm()
    assert helm.helm_name() == "ReasoningHelm"


async def test_reasoning_helm_step_yields_when_interaction_missing():
    """Without a valid interaction on the visitor, the helm cannot operate
    and must signal Bridge to finalise the turn cleanly."""
    helm = ReasoningHelm()
    visitor = MagicMock()
    visitor.interaction = None
    visitor.conversation = None
    visitor.response_bus = MagicMock()
    visitor.session_id = "sess_test"
    visitor.user_id = "u_test"
    visitor.utterance = "hi"

    bridge_state = MagicMock()
    bridge_state.helm_states = {}

    # _ensure_interaction returns False when interaction is missing; the
    # orchestration body returns early and step() defaults to YIELD.
    result = await helm.step(visitor, bridge_state)
    assert isinstance(result, YIELD)
