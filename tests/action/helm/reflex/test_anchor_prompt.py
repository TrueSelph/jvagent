"""Tests for the anchor-aware Reflex peer-awareness prompt (ADR-0009 §5).

Pin the load-bearing shape:

1. Each anchor-routable IA renders as a multi-line block with name,
   description, and anchors.
2. Pattern orchestrators, always-execute IAs, chain-internal IAs, and
   turn-locked IAs are excluded from the rendered catalog.
3. ``ANCHOR_DISAMBIGUATION_CLAUSE`` is embedded verbatim in the system
   prompt (cross-module invariant — also pinned in
   ``tests/action/router/test_anchor_disambiguation.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.helm.reflex.prompts import (
    ANCHOR_DISAMBIGUATION_CLAUSE,
    REFLEX_SYSTEM_PROMPT,
    render_peer_action_block,
)
from jvagent.action.helm.reflex.reflex_helm import ReflexHelm
from jvagent.action.manifest import Manifest


def _build_ia(
    cls_name: str,
    *,
    purpose: str = "",
    anchors: List[str] | None = None,
    always_execute: bool = False,
    routable_by_anchor: bool = True,
    turn_lock: bool = False,
    pattern_orchestrator: bool = False,
) -> Any:
    """Build a mock InteractAction-like object with manifest + anchors."""
    from jvagent.action.interact.base import InteractAction

    ia = MagicMock(spec=InteractAction)
    ia.__class__ = type(cls_name, (InteractAction,), {})
    manifest = Manifest.from_payload(
        {
            "purpose": purpose,
            "routable_by_anchor": routable_by_anchor,
            "turn_lock": turn_lock,
            "pattern_orchestrator": pattern_orchestrator,
        }
    )
    ia.get_manifest = MagicMock(return_value=manifest)
    ia.always_execute = always_execute
    ia.anchors = anchors or []
    ia.get_anchors = AsyncMock(return_value=None)
    return ia


class TestPeerActionBlockRendering:
    def test_block_includes_name_description_and_anchors(self):
        block = render_peer_action_block(
            "SignupInterview",
            description="enroll the user",
            anchors=["user wants to sign up", "user wants to register"],
        )
        assert "- SignupInterview" in block
        assert "description: enroll the user" in block
        assert "anchors:" in block
        assert "      - user wants to sign up" in block
        assert "      - user wants to register" in block

    def test_block_omits_anchor_subblock_when_empty(self):
        block = render_peer_action_block(
            "AnchorlessIA",
            description="some flow",
            anchors=[],
        )
        assert "- AnchorlessIA" in block
        assert "description: some flow" in block
        assert "anchors:" not in block

    def test_block_handles_empty_description(self):
        block = render_peer_action_block(
            "Foo",
            description="",
            anchors=["a", "b"],
        )
        assert "description: (no description declared)" in block


class TestSystemPromptEmbedding:
    def test_system_prompt_embeds_anchor_disambiguation_clause(self):
        rendered = REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section="(no peer helms installed)",
            helms_available_section="",
            peer_actions_section="(no anchor-routable flows installed)",
            anchor_disambiguation_clause=ANCHOR_DISAMBIGUATION_CLAUSE,
        )
        assert ANCHOR_DISAMBIGUATION_CLAUSE in rendered

    def test_system_prompt_has_anchor_routable_flows_header(self):
        rendered = REFLEX_SYSTEM_PROMPT.format(
            peer_helms_section="-",
            helms_available_section="",
            peer_actions_section="-",
            anchor_disambiguation_clause=ANCHOR_DISAMBIGUATION_CLAUSE,
        )
        assert "ANCHOR-ROUTABLE FLOWS:" in rendered


class TestPeerActionFiltering:
    """``ReflexHelm._collect_peer_actions`` excludes the right categories."""

    @pytest.mark.asyncio
    async def test_excludes_pattern_orchestrator(self):
        helm = ReflexHelm()
        bridge = _build_ia(
            "BridgeInteractAction",
            purpose="orchestrator",
            anchors=["unused"],
            pattern_orchestrator=True,
            routable_by_anchor=False,
        )
        agent = self._mock_agent([bridge])
        actions = await helm._collect_peer_actions(agent)
        assert actions == []

    @pytest.mark.asyncio
    async def test_excludes_always_execute(self):
        helm = ReflexHelm()
        intro = _build_ia(
            "IntroInteractAction",
            purpose="intro",
            anchors=["used"],
            always_execute=True,
        )
        agent = self._mock_agent([intro])
        actions = await helm._collect_peer_actions(agent)
        assert actions == []

    @pytest.mark.asyncio
    async def test_excludes_chain_internal(self):
        helm = ReflexHelm()
        internal = _build_ia(
            "ConfirmPayment",
            purpose="confirm",
            anchors=["unused"],
            routable_by_anchor=False,
        )
        agent = self._mock_agent([internal])
        actions = await helm._collect_peer_actions(agent)
        assert actions == []

    @pytest.mark.asyncio
    async def test_includes_turn_locked(self):
        # Turn-locked IAs need anchor entry on first turn; auto-DELEGATE
        # via find_turn_lock_owner only fires for mid-flight turns once
        # the lock is acquired. Excluding them from Reflex's catalog
        # left first-entry to Reasoning every time (the gap user
        # reported post-Wave-9).
        helm = ReflexHelm()
        interview = _build_ia(
            "InterviewInteractAction",
            purpose="multi-turn interview",
            anchors=["user agrees to interview"],
            turn_lock=True,
        )
        agent = self._mock_agent([interview])
        actions = await helm._collect_peer_actions(agent)
        assert len(actions) == 1
        assert actions[0]["name"] == "InterviewInteractAction"
        assert actions[0]["anchors"] == ["user agrees to interview"]

    @pytest.mark.asyncio
    async def test_includes_anchor_routable_conversational(self):
        helm = ReflexHelm()
        signup = _build_ia(
            "SignupInterviewInteractAction",
            purpose="enroll user in training",
            anchors=["user wants to enroll"],
        )
        agent = self._mock_agent([signup])
        actions = await helm._collect_peer_actions(agent)
        assert len(actions) == 1
        assert actions[0]["name"] == "SignupInterviewInteractAction"
        assert actions[0]["description"] == "enroll user in training"
        assert actions[0]["anchors"] == ["user wants to enroll"]

    @pytest.mark.asyncio
    async def test_dynamic_anchors_override_static(self):
        helm = ReflexHelm()
        ia = _build_ia(
            "DynamicIA",
            purpose="dynamic anchors",
            anchors=["static anchor"],
        )
        ia.get_anchors = AsyncMock(return_value=["dynamic anchor"])
        agent = self._mock_agent([ia])
        actions = await helm._collect_peer_actions(agent, conversation=object())
        assert actions[0]["anchors"] == ["dynamic anchor"]

    @pytest.mark.asyncio
    async def test_anchorless_ia_without_description_is_dropped(self):
        helm = ReflexHelm()
        ia = _build_ia("EmptyIA", purpose="", anchors=[])
        agent = self._mock_agent([ia])
        actions = await helm._collect_peer_actions(agent)
        assert actions == []

    @pytest.mark.asyncio
    async def test_anchorless_ia_with_description_is_kept(self):
        # Anchorless conversational IAs are still listed when they have
        # a description — Reflex matches on description as a fallback,
        # and the engine escape hatch is the recovery path.
        helm = ReflexHelm()
        ia = _build_ia("DescOnlyIA", purpose="some flow", anchors=[])
        agent = self._mock_agent([ia])
        actions = await helm._collect_peer_actions(agent)
        assert len(actions) == 1
        assert actions[0]["anchors"] == []

    def _mock_agent(self, actions: List[Any]) -> Any:
        actions_mgr = MagicMock()
        actions_mgr.get_all_actions = AsyncMock(return_value=actions)
        agent = MagicMock()
        agent.get_actions_manager = AsyncMock(return_value=actions_mgr)
        return agent
