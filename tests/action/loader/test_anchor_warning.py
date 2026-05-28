"""Tests for the anchorless-routable-IA bootstrap warning (ADR-0009 §6)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from jvagent.action.loader.action_loader import _warn_if_anchorless_routable_ia
from jvagent.action.manifest import Manifest


def _build_ia_action(
    *,
    purpose: str = "p",
    anchors=None,
    always_execute: bool = False,
    routable_by_anchor: bool = True,
    turn_lock: bool = False,
    pattern_orchestrator: bool = False,
):
    from jvagent.action.interact.base import InteractAction

    action = MagicMock(spec=InteractAction)
    manifest = Manifest.from_payload(
        {
            "purpose": purpose,
            "routable_by_anchor": routable_by_anchor,
            "turn_lock": turn_lock,
            "pattern_orchestrator": pattern_orchestrator,
        }
    )
    action.get_manifest = MagicMock(return_value=manifest)
    action.always_execute = always_execute
    action.anchors = anchors or []
    return action


def _metadata(namespace: str = "jvagent", name: str = "foo_ia"):
    md = MagicMock()
    md.namespace = namespace
    md.name = name
    return md


def test_warning_fires_for_anchorless_anchor_routable_ia(caplog):
    action = _build_ia_action(purpose="some flow", anchors=[])
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(
            action, _metadata("ns", "anchorless_ia"), "agent_x"
        )
    matches = [r for r in caplog.records if "no anchors declared" in r.getMessage()]
    assert matches, f"expected anchorless warning in records: {caplog.records}"
    msg = matches[0].getMessage()
    assert "ns/anchorless_ia" in msg
    assert "agent_x" in msg


def test_no_warning_when_anchors_present(caplog):
    action = _build_ia_action(anchors=["user wants thing"])
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(action, _metadata(), "agent_y")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)


def test_no_warning_for_pattern_orchestrator(caplog):
    action = _build_ia_action(
        anchors=[], pattern_orchestrator=True, routable_by_anchor=False
    )
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(action, _metadata("ns", "bridge"), "agent")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)


def test_no_warning_for_always_execute(caplog):
    action = _build_ia_action(anchors=[], always_execute=True)
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(action, _metadata("ns", "intro"), "agent")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)


def test_no_warning_for_chain_internal(caplog):
    action = _build_ia_action(anchors=[], routable_by_anchor=False)
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(
            action, _metadata("ns", "confirm_payment"), "agent"
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)


def test_no_warning_for_turn_locked(caplog):
    action = _build_ia_action(anchors=[], turn_lock=True)
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(action, _metadata("ns", "interview"), "agent")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)


def test_no_warning_for_non_interact_action(caplog):
    # A plain MagicMock — not an InteractAction — should be ignored.
    action = MagicMock()
    action.get_manifest = MagicMock(
        return_value=Manifest.from_payload({"routable_by_anchor": True})
    )
    action.always_execute = False
    action.anchors = []
    with caplog.at_level(logging.WARNING, logger="jvagent.action.loader.action_loader"):
        _warn_if_anchorless_routable_ia(action, _metadata(), "agent")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("no anchors declared" in m for m in msgs)
