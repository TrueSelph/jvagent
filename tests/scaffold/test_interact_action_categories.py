"""Tests for the ADR-0009 IA-category scaffolder templates."""

from __future__ import annotations

import pytest

from jvagent.action.manifest import Manifest
from jvagent.scaffold.interact_action import (
    CATEGORY_ALWAYS_EXECUTE,
    CATEGORY_ANCHOR_ROUTABLE,
    CATEGORY_CHAIN_INTERNAL,
    CATEGORY_PATTERN_ORCHESTRATOR,
    CATEGORY_SPECS,
    CATEGORY_SYNCHRONOUS,
    CATEGORY_TURN_LOCKED,
    VALID_CATEGORIES,
    build_manifest_payload,
)


class TestCategoryRegistry:
    def test_six_categories_registered(self):
        assert set(VALID_CATEGORIES) == {
            CATEGORY_ANCHOR_ROUTABLE,
            CATEGORY_CHAIN_INTERNAL,
            CATEGORY_ALWAYS_EXECUTE,
            CATEGORY_SYNCHRONOUS,
            CATEGORY_PATTERN_ORCHESTRATOR,
            CATEGORY_TURN_LOCKED,
        }

    def test_anchor_routable_marks_anchors_required(self):
        assert CATEGORY_SPECS[CATEGORY_ANCHOR_ROUTABLE].requires_anchors is True

    def test_pattern_orchestrator_marks_confirmation_required(self):
        spec = CATEGORY_SPECS[CATEGORY_PATTERN_ORCHESTRATOR]
        assert spec.requires_pattern_orchestrator_confirmation is True

    def test_others_do_not_require_anchors(self):
        for cat in (
            CATEGORY_CHAIN_INTERNAL,
            CATEGORY_ALWAYS_EXECUTE,
            CATEGORY_SYNCHRONOUS,
            CATEGORY_PATTERN_ORCHESTRATOR,
            CATEGORY_TURN_LOCKED,
        ):
            assert CATEGORY_SPECS[cat].requires_anchors is False


class TestAnchorRoutableTemplate:
    def test_minimum_three_anchors_required(self):
        with pytest.raises(ValueError, match="at least 3"):
            build_manifest_payload(
                CATEGORY_ANCHOR_ROUTABLE,
                purpose="enroll user",
                anchors=["only one"],
            )

    def test_happy_path_writes_anchors(self):
        payload = build_manifest_payload(
            CATEGORY_ANCHOR_ROUTABLE,
            purpose="enroll user in training",
            anchors=[
                "user wants to enroll",
                "user wants to sign up",
                "user wants to register",
            ],
        )
        m = Manifest.from_payload(payload)
        assert m.routable_by_anchor is True
        assert m.activates_on == [
            "user wants to enroll",
            "user wants to sign up",
            "user wants to register",
        ]
        assert m.pattern_orchestrator is False
        assert m.purpose == "enroll user in training"


class TestChainInternalTemplate:
    def test_sets_routable_by_anchor_false(self):
        payload = build_manifest_payload(
            CATEGORY_CHAIN_INTERNAL,
            purpose="confirm payment",
        )
        m = Manifest.from_payload(payload)
        assert m.routable_by_anchor is False
        assert m.pattern_orchestrator is False
        assert m.turn_lock is False


class TestAlwaysExecuteTemplate:
    def test_marks_routable_by_anchor_false(self):
        payload = build_manifest_payload(
            CATEGORY_ALWAYS_EXECUTE,
            purpose="audit every interaction",
        )
        m = Manifest.from_payload(payload)
        assert m.routable_by_anchor is False
        # always_execute itself is a class attribute, not a manifest field.
        # Author must set it on the InteractAction subclass.


class TestSynchronousTemplate:
    def test_requires_return_value_description(self):
        with pytest.raises(ValueError, match="return_value_description"):
            build_manifest_payload(
                CATEGORY_SYNCHRONOUS,
                purpose="lookup order status",
            )

    def test_embeds_return_contract_in_purpose(self):
        payload = build_manifest_payload(
            CATEGORY_SYNCHRONOUS,
            purpose="lookup order status",
            return_value_description="JSON {order_id, status, eta}",
        )
        m = Manifest.from_payload(payload)
        assert m.routable_by_anchor is False
        assert "Returns:" in m.purpose
        assert "JSON {order_id, status, eta}" in m.purpose


class TestPatternOrchestratorTemplate:
    def test_sets_orchestrator_flag(self):
        payload = build_manifest_payload(
            CATEGORY_PATTERN_ORCHESTRATOR,
            purpose="bridge orchestrator",
        )
        m = Manifest.from_payload(payload)
        assert m.pattern_orchestrator is True
        assert m.routable_by_anchor is False


class TestTurnLockedTemplate:
    def test_sets_turn_lock(self):
        payload = build_manifest_payload(
            CATEGORY_TURN_LOCKED,
            purpose="multi-turn interview",
            anchors=[
                "user agrees to interview",
                "user wants to be interviewed",
                "user starts feedback",
            ],
        )
        m = Manifest.from_payload(payload)
        assert m.turn_lock is True
        # Anchors are still optional entry path on first turn.
        assert m.activates_on == [
            "user agrees to interview",
            "user wants to be interviewed",
            "user starts feedback",
        ]


def test_unknown_category_raises():
    with pytest.raises(ValueError, match="unknown category"):
        build_manifest_payload("not_a_category", purpose="x")
