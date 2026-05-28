"""Tests for the ADR-0009 ``delegate_to_ia`` engine recovery-hatch tool.

Pins:

1. Eligibility filter excludes pattern orchestrators and always-execute IAs.
2. Tool description lists every eligible IA with its description.
3. Calling the tool appends to ``pending_ias`` and finalizes the session.
4. Unknown name returns an error with the available list.
5. Bridge-state-less visitors fail gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.reasoning.context import EngineContext
from jvagent.action.helm.reasoning.tools.delegate_to_ia import (
    _build_delegate_to_ia_tools,
    _enumerate_eligible_ias,
)
from jvagent.action.manifest import Manifest


def _make_ia(
    cls_name: str,
    *,
    purpose: str = "",
    always_execute: bool = False,
    pattern_orchestrator: bool = False,
    routable_by_anchor: bool = True,
):
    from jvagent.action.interact.base import InteractAction

    ia = MagicMock(spec=InteractAction)
    ia.__class__ = type(cls_name, (InteractAction,), {})
    manifest = Manifest.from_payload(
        {
            "purpose": purpose,
            "pattern_orchestrator": pattern_orchestrator,
            "routable_by_anchor": routable_by_anchor,
        }
    )
    ia.get_manifest = MagicMock(return_value=manifest)
    ia.always_execute = always_execute
    return ia


def _make_ctx(visitor=None, agent=None) -> EngineContext:
    """Build a barely-valid EngineContext stub for the tool."""
    return EngineContext(
        utterance="",
        conversation=None,
        interaction=None,
        agent=agent,
        model_action=None,
        config=MagicMock(),
        response_bus=None,
        session_id="",
        channel="default",
        stream=False,
        user_id=None,
        persona=None,
        action=None,
        visitor=visitor,
        preloaded_skills=[],
    )


def _agent_with_actions(actions):
    actions_mgr = MagicMock()
    actions_mgr.get_all_actions = AsyncMock(return_value=actions)
    agent = MagicMock()
    agent.get_actions_manager = AsyncMock(return_value=actions_mgr)
    return agent


class TestEligibilityFilter:
    @pytest.mark.asyncio
    async def test_excludes_pattern_orchestrator(self):
        bridge = _make_ia(
            "BridgeInteractAction",
            purpose="orchestrator",
            pattern_orchestrator=True,
            routable_by_anchor=False,
        )
        ctx = _make_ctx(agent=_agent_with_actions([bridge]))
        eligible = await _enumerate_eligible_ias(ctx)
        assert eligible == []

    @pytest.mark.asyncio
    async def test_excludes_always_execute(self):
        intro = _make_ia("IntroIA", purpose="intro", always_execute=True)
        ctx = _make_ctx(agent=_agent_with_actions([intro]))
        eligible = await _enumerate_eligible_ias(ctx)
        assert eligible == []

    @pytest.mark.asyncio
    async def test_includes_anchorless_conversational(self):
        ia = _make_ia("DescOnlyIA", purpose="some flow")
        ctx = _make_ctx(agent=_agent_with_actions([ia]))
        eligible = await _enumerate_eligible_ias(ctx)
        assert len(eligible) == 1
        assert eligible[0].__class__.__name__ == "DescOnlyIA"

    @pytest.mark.asyncio
    async def test_includes_chain_internal_ias(self):
        # Chain-internal IAs (routable_by_anchor=false) are still
        # delegate_to_ia-targetable — the model recognising intent and
        # explicitly invoking them is fine, even though Reflex's
        # peer-awareness skips them.
        ia = _make_ia("ChainIA", purpose="chain step", routable_by_anchor=False)
        ctx = _make_ctx(agent=_agent_with_actions([ia]))
        eligible = await _enumerate_eligible_ias(ctx)
        assert len(eligible) == 1


class TestToolDescription:
    @pytest.mark.asyncio
    async def test_lists_every_eligible_ia(self):
        ias = [
            _make_ia("Handoff", purpose="handoff to human"),
            _make_ia("ResetPwd", purpose="reset password flow"),
        ]
        ctx = _make_ctx(agent=_agent_with_actions(ias))
        tools = _build_delegate_to_ia_tools(ctx, ias)
        assert len(tools) == 1
        desc = tools[0].description
        assert "Handoff: handoff to human" in desc
        assert "ResetPwd: reset password flow" in desc


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_appends_to_pending_ias_and_finalizes(self):
        ia = _make_ia("HandoffIA", purpose="handoff")

        # Build a visitor with BridgeState + EngineSession plumbing.
        visitor = MagicMock()
        bridge_state = MagicMock()
        bridge_state.helm_states = {}
        from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR

        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, bridge_state)
        visitor._skill_state = {}

        ctx = _make_ctx(visitor=visitor, agent=_agent_with_actions([ia]))
        tool = _build_delegate_to_ia_tools(ctx, [ia])[0]

        result = await tool.execute(name="HandoffIA")
        assert result == {"ok": True, "delegated_to": "HandoffIA"}
        assert bridge_state.helm_states["ReasoningHelm"]["pending_ias"] == ["HandoffIA"]

        from jvagent.action.helm.reasoning.session import get_session_optional

        session = get_session_optional(visitor)
        assert session is not None
        assert session.finalized is True

    @pytest.mark.asyncio
    async def test_unknown_name_returns_error_with_available_list(self):
        ia = _make_ia("RealIA", purpose="real")
        visitor = MagicMock()
        bridge_state = MagicMock()
        bridge_state.helm_states = {}
        from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR

        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, bridge_state)
        visitor._skill_state = {}

        ctx = _make_ctx(visitor=visitor, agent=_agent_with_actions([ia]))
        tool = _build_delegate_to_ia_tools(ctx, [ia])[0]
        result = await tool.execute(name="NonExistentIA")
        assert "error" in result
        assert result["available"] == ["RealIA"]
        # No side effects.
        assert bridge_state.helm_states == {}

    @pytest.mark.asyncio
    async def test_no_bridge_state_returns_error(self):
        ia = _make_ia("AnyIA", purpose="any")
        visitor = MagicMock(spec=[])  # no _bridge_state attribute
        visitor._skill_state = {}
        ctx = _make_ctx(visitor=visitor, agent=_agent_with_actions([ia]))
        tool = _build_delegate_to_ia_tools(ctx, [ia])[0]
        result = await tool.execute(name="AnyIA")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_name_arg_returns_error(self):
        ia = _make_ia("AnyIA", purpose="any")
        visitor = MagicMock()
        bridge_state = MagicMock()
        bridge_state.helm_states = {}
        from jvagent.action.bridge.state import BRIDGE_STATE_VISITOR_ATTR

        setattr(visitor, BRIDGE_STATE_VISITOR_ATTR, bridge_state)
        visitor._skill_state = {}

        ctx = _make_ctx(visitor=visitor, agent=_agent_with_actions([ia]))
        tool = _build_delegate_to_ia_tools(ctx, [ia])[0]
        result = await tool.execute(name="")
        assert "error" in result
