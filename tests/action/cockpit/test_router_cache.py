"""Tests for the cockpit router cache helpers (Phase 3 latency optimisation).

Covers ``CockpitRouter._build_cache_key`` + ``_restore_cached_routing_result``.
The end-to-end cache hit/miss path through ``_run_llm_route`` is exercised
by integration tests; these tests pin the helper contract directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jvagent.action.cockpit.routing.router import CockpitRouter
from jvagent.action.cockpit.routing.types import POSTURE_RESPOND, RoutingResult


def _make_router_with_visitor(active_tasks=None):
    action = MagicMock()
    router = CockpitRouter(action)
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=active_tasks or [])
    router._visitor = visitor
    return router, visitor


def _interaction(utterance: str = "Hi"):
    interaction = MagicMock()
    interaction.utterance = utterance
    return interaction


def _conversation(conv_id: str = "conv_1"):
    conv = MagicMock()
    conv.id = conv_id
    return conv


# ----------------------------------------------------------------------
# _build_cache_key
# ----------------------------------------------------------------------


def test_build_cache_key_returns_none_without_conversation() -> None:
    router, _ = _make_router_with_visitor()
    assert router._build_cache_key(_interaction(), conversation=None) is None


def test_build_cache_key_returns_none_for_empty_utterance() -> None:
    router, _ = _make_router_with_visitor()
    assert router._build_cache_key(_interaction(""), _conversation()) is None
    assert router._build_cache_key(_interaction("   "), _conversation()) is None


def test_build_cache_key_returns_none_when_conversation_id_missing() -> None:
    router, _ = _make_router_with_visitor()
    conv = MagicMock()
    conv.id = ""
    assert router._build_cache_key(_interaction(), conv) is None


def test_build_cache_key_stable_across_calls_with_same_inputs() -> None:
    router, _ = _make_router_with_visitor()
    key_a = router._build_cache_key(_interaction("Hello"), _conversation("c1"))
    key_b = router._build_cache_key(_interaction("Hello"), _conversation("c1"))
    assert key_a == key_b
    assert isinstance(key_a, str) and len(key_a) > 0


def test_build_cache_key_differs_when_utterance_changes() -> None:
    router, _ = _make_router_with_visitor()
    k1 = router._build_cache_key(_interaction("Hello"), _conversation("c1"))
    k2 = router._build_cache_key(_interaction("Goodbye"), _conversation("c1"))
    assert k1 != k2


def test_build_cache_key_differs_when_active_tasks_differ() -> None:
    """Active-task fingerprint ensures fragments mid-interview don't share
    cache keys with the same fragment after the interview ends."""
    handle = MagicMock()
    handle.owner_action = "ReportInterviewInteractAction"
    handle.data = {"state": "active"}

    router_no_tasks, _ = _make_router_with_visitor(active_tasks=[])
    router_with_task, _ = _make_router_with_visitor(active_tasks=[handle])

    k_empty = router_no_tasks._build_cache_key(_interaction("Yes"), _conversation("c1"))
    k_task = router_with_task._build_cache_key(_interaction("Yes"), _conversation("c1"))
    assert k_empty != k_task


# ----------------------------------------------------------------------
# _restore_cached_routing_result
# ----------------------------------------------------------------------


def test_restore_cached_routing_result_validates_routes() -> None:
    """Routes that drifted out of the catalog since the cache write are dropped."""
    router, _ = _make_router_with_visitor()
    cached = RoutingResult(
        posture=POSTURE_RESPOND,
        intent_type="DIRECTIVE",
        actions=["converse", "stale_skill"],
        interact_actions=["LiveIA", "stale_ia"],
        confidence=0.9,
    ).to_dict()

    skill_descriptors = {"converse": {"description": ""}}
    ia_descriptors = {"LiveIA": {"description": ""}}

    restored = router._restore_cached_routing_result(
        cached, skill_descriptors, ia_descriptors
    )
    assert restored is not None
    assert restored.actions == ["converse"]  # stale_skill dropped
    assert restored.interact_actions == ["LiveIA"]  # stale_ia dropped


def test_restore_cached_routing_result_returns_none_on_bad_payload() -> None:
    router, _ = _make_router_with_visitor()
    # ``confidence`` is parsed defensively, but a fundamentally bad shape
    # (None) should not raise — the helper returns None so the caller
    # falls through to the LLM path.
    restored = router._restore_cached_routing_result(
        None,  # type: ignore[arg-type]
        skill_descriptors={"converse": {"description": ""}},
        interact_action_descriptors={},
    )
    assert restored is None
