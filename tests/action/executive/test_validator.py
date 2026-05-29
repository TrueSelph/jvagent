"""Mutual-exclusivity validator tests (ADR-0010 §3 inv. 7 / SPEC §11 inv. 9).

Synchronous — kept out of ``test_loop.py`` so they don't inherit its
module-level ``asyncio`` mark.
"""

from __future__ import annotations

from jvagent.action.executive.executive_interact_action import detect_pattern_conflict


def test_detect_pattern_conflict_with_bridge():
    msg = detect_pattern_conflict(["ExecutiveInteractAction", "BridgeInteractAction"])
    assert msg is not None and "BridgeInteractAction" in msg


def test_detect_pattern_conflict_with_cockpit():
    msg = detect_pattern_conflict(
        ["ExecutiveInteractAction", "CockpitInteractAction", "IntroInteractAction"]
    )
    assert msg is not None and "CockpitInteractAction" in msg


def test_detect_pattern_conflict_clean():
    assert (
        detect_pattern_conflict(["ExecutiveInteractAction", "IntroInteractAction"])
        is None
    )
    assert detect_pattern_conflict(["BridgeInteractAction"]) is None
    assert detect_pattern_conflict([]) is None
