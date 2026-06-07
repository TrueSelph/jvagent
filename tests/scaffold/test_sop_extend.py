"""Tests for SOP inheritance via extends (ADR-0020)."""

from __future__ import annotations

import pytest

from jvagent.scaffold.sop_extend import (
    compose_extended_sop_bodies,
    compose_skill_body,
    load_action_base_sop_body,
    parse_extends_ref,
    reset_sop_extend_cache,
)


def setup_function() -> None:
    reset_sop_extend_cache()


def test_parse_extends_ref_action():
    assert parse_extends_ref("action:jvagent/interview_action") == (
        "action",
        "jvagent/interview_action",
    )


def test_parse_extends_ref_skill():
    assert parse_extends_ref("skill:base_skill") == ("skill", "base_skill")


def test_parse_extends_ref_invalid():
    assert parse_extends_ref("jvagent/interview_action") is None
    assert parse_extends_ref("") is None


def test_load_action_base_sop_body_interview():
    body = load_action_base_sop_body("jvagent/interview_action")
    assert "Standard Interview Procedure" in body
    assert "interview__set_field" in body


def test_compose_skill_body():
    composed = compose_skill_body("Base", "Custom")
    assert composed == "Base\n\nCustom"
    assert compose_skill_body("Base", "") == "Base"
    assert compose_skill_body("", "Custom") == "Custom"


def test_compose_extended_sop_bodies_action_extends():
    bundles = {
        "child": {
            "name": "child",
            "content": "## Custom\n\nRules.",
            "extends": "action:jvagent/interview_action",
        }
    }
    out = compose_extended_sop_bodies(bundles)
    assert "Standard Interview Procedure" in out["child"]["content"]
    assert "## Custom" in out["child"]["content"]
    assert "Rules." in out["child"]["content"]


def test_compose_extended_sop_bodies_skill_chain():
    bundles = {
        "base": {
            "name": "base",
            "content": "Base custom.",
            "extends": "action:jvagent/interview_action",
        },
        "child": {
            "name": "child",
            "content": "Child custom.",
            "extends": "skill:base",
        },
    }
    out = compose_extended_sop_bodies(bundles)
    child = out["child"]["content"]
    assert "Standard Interview Procedure" in child
    assert "Base custom." in child
    assert child.endswith("Child custom.")


def test_compose_extended_sop_bodies_cycle_raises():
    bundles = {
        "a": {"name": "a", "content": "A", "extends": "skill:b"},
        "b": {"name": "b", "content": "B", "extends": "skill:a"},
    }
    with pytest.raises(ValueError, match="cycle"):
        compose_extended_sop_bodies(bundles)


def test_compose_extended_sop_bodies_missing_target_warns():
    bundles = {
        "orphan": {
            "name": "orphan",
            "content": "Only custom.",
            "extends": "action:jvagent/nonexistent_action_xyz",
        }
    }
    out = compose_extended_sop_bodies(bundles)
    assert out["orphan"]["content"] == "Only custom."
