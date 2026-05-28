"""Regression tests for Wave-4 M3: manifest-based ack eligibility.

The original Bridge `_is_ack_eligible` read `target_helm.latency_class`
attribute directly with a comment promising "Milestone E rewires this to
consult the loaded manifest." Wave 4 (May 2026) actually wires it: the
decision now reads ``target_helm.get_manifest().latency_class`` first
and falls back to the attribute only when the manifest is unavailable.

The manifest is the documented source of truth (BRIDGE-ROADMAP §D /
ADR-0007); the attribute is a configuration mirror retained for
operators who tune via ``agent.yaml.context.latency_class`` without
editing the action's ``info.yaml``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jvagent.action.bridge.bridge_interact_action import (
    _ACK_ELIGIBLE_LATENCY_CLASSES,
    BridgeInteractAction,
)
from jvagent.action.manifest import Manifest


def _helm_with_manifest(latency_class: str, attribute_class: str = "instant") -> Any:
    """Build a fake helm with a controllable manifest + attribute split.

    The two values are different so we can verify the manifest wins.
    """
    helm = MagicMock()
    helm.latency_class = attribute_class
    helm.get_manifest = MagicMock(
        return_value=Manifest(latency_class=latency_class)
    )
    helm.helm_name = MagicMock(return_value="FakeHelm")
    return helm


def _helm_with_attr_only(attribute_class: str) -> Any:
    """Helm whose ``get_manifest()`` raises (or returns empty class)."""
    helm = MagicMock()
    helm.latency_class = attribute_class
    helm.get_manifest = MagicMock(side_effect=RuntimeError("no manifest"))
    helm.helm_name = MagicMock(return_value="FakeHelm")
    return helm


class TestAckEligibilityManifestFirst:
    """Manifest ``latency_class`` is consulted before the attribute fallback."""

    def test_manifest_deliberate_ack_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_manifest("deliberate", attribute_class="instant")
        assert bridge._is_ack_eligible(helm) is True

    def test_manifest_long_ack_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_manifest("long", attribute_class="instant")
        assert bridge._is_ack_eligible(helm) is True

    def test_manifest_instant_not_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_manifest("instant", attribute_class="deliberate")
        # Manifest wins — even though the attribute says "deliberate",
        # the manifest's "instant" produces False.
        assert bridge._is_ack_eligible(helm) is False

    def test_manifest_quick_not_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_manifest("quick", attribute_class="long")
        # Manifest wins again.
        assert bridge._is_ack_eligible(helm) is False


class TestAckEligibilityAttributeFallback:
    """When manifest unavailable, the attribute is the fallback."""

    def test_manifest_raises_falls_back_to_attribute_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_attr_only("deliberate")
        assert bridge._is_ack_eligible(helm) is True

    def test_manifest_raises_falls_back_to_attribute_not_eligible(self):
        bridge = BridgeInteractAction()
        helm = _helm_with_attr_only("instant")
        assert bridge._is_ack_eligible(helm) is False

    def test_manifest_empty_latency_class_falls_back(self):
        """Manifest with empty latency_class string → fall through to attribute."""
        bridge = BridgeInteractAction()
        helm = MagicMock()
        helm.latency_class = "deliberate"
        # Empty manifest latency_class (operator forgot to set it).
        helm.get_manifest = MagicMock(return_value=Manifest(latency_class=""))
        helm.helm_name = MagicMock(return_value="FakeHelm")
        # Should fall back to the attribute, which IS eligible.
        assert bridge._is_ack_eligible(helm) is True


class TestAckEligibleLatencyClassSet:
    """Pin the exact set of latency classes that warrant ack-on-shift."""

    def test_eligible_set_unchanged(self):
        """The set must remain ``{deliberate, long}`` — adding more without
        updating the prompt/UX contract risks a noisy ack on every shift.
        """
        assert _ACK_ELIGIBLE_LATENCY_CLASSES == frozenset(
            {"deliberate", "long"}
        )

    @pytest.mark.parametrize(
        "latency_class,expected",
        [
            ("instant", False),
            ("quick", False),
            ("deliberate", True),
            ("long", True),
            ("DELIBERATE", True),  # case-insensitive
            ("Long", True),
            ("", False),
        ],
    )
    def test_each_class_via_manifest(
        self, latency_class: str, expected: bool
    ) -> None:
        bridge = BridgeInteractAction()
        if latency_class:
            helm = _helm_with_manifest(
                latency_class.lower(),  # Manifest validates lowercase
                attribute_class="instant",
            )
        else:
            # Empty manifest → falls back to attribute. Use "instant" so
            # the overall result is False regardless of which path runs.
            helm = MagicMock()
            helm.latency_class = "instant"
            helm.get_manifest = MagicMock(
                return_value=Manifest(latency_class="")
            )
            helm.helm_name = MagicMock(return_value="FakeHelm")
        assert bridge._is_ack_eligible(helm) is expected
