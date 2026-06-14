"""Tests for SOP inheritance via extends (ADR-0020)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.scaffold.sop_extend import (
    compose_extended_sop_bodies,
    compose_skill_body,
    inherit_extends_lock_companions,
    inherit_extends_task_lock,
    load_action_base_sop_body,
    merge_extends_allowed_tools,
    parse_extends_ref,
    reset_sop_extend_cache,
)

_CORE_ENV = "JVAGENT_CORE_ACTION_PATH"


def _make_core_action_root(root: Path, marker: str) -> Path:
    action_dir = root / "interview"
    action_dir.mkdir(parents=True, exist_ok=True)
    (action_dir / "info.yaml").write_text(
        "package:\n  name: jvagent/interview\n",
        encoding="utf-8",
    )
    (action_dir / "SKILL.md").write_text(
        f"# Standard Interview Procedure\n\n{marker}\n",
        encoding="utf-8",
    )
    return root


def setup_function() -> None:
    reset_sop_extend_cache()


def test_parse_extends_ref_action():
    assert parse_extends_ref("action:jvagent/interview") == (
        "action",
        "jvagent/interview",
    )


def test_parse_extends_ref_skill():
    assert parse_extends_ref("skill:base_skill") == ("skill", "base_skill")


def test_parse_extends_ref_invalid():
    assert parse_extends_ref("jvagent/interview") is None
    assert parse_extends_ref("") is None


def test_load_action_base_sop_body_interview():
    body = load_action_base_sop_body("jvagent/interview")
    assert "Standard Interview Procedure" in body
    assert "interview__set_fields" in body


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
            "extends": "action:jvagent/interview",
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
            "extends": "action:jvagent/interview",
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


def test_merge_extends_allowed_tools_from_action_base():
    bundles = {
        "signup_interview": {
            "name": "signup_interview",
            "content": "Custom.",
            "extends": "action:jvagent/interview",
            "allowed_tools_add": [],
            "disabled_tools": [],
        }
    }
    out = merge_extends_allowed_tools(bundles)
    tools = out["signup_interview"]["allowed_tools"]
    assert "interview__set_fields" in tools
    assert "interview__reset" in tools
    assert "interview__cancel" in tools


def test_merge_extends_allowed_tools_additive_and_disabled():
    bundles = {
        "child": {
            "name": "child",
            "content": "Custom.",
            "extends": "action:jvagent/interview",
            "allowed_tools_add": ["child__custom_tool"],
            "disabled_tools": ["interview__reset"],
        }
    }
    out = merge_extends_allowed_tools(bundles)
    tools = out["child"]["allowed_tools"]
    assert "interview__set_fields" in tools
    assert "child__custom_tool" in tools
    assert "interview__reset" not in tools


def test_inherit_extends_task_lock_from_action_base():
    """A skill that extends the interview action inherits its task-lock so the
    orchestrator's turn-lock resolver can bind it each turn (regression: lock was
    silently dropped because task_lock didn't propagate through extends)."""
    bundles = {
        "signup_interview": {
            "name": "signup_interview",
            "content": "Custom.",
            "extends": "action:jvagent/interview",
            "task_lock": False,
        }
    }
    out = inherit_extends_task_lock(bundles)
    assert out["signup_interview"]["task_lock"] is True


def test_inherit_extends_task_lock_skill_chain():
    bundles = {
        "base_skill": {"name": "base_skill", "content": "b", "task_lock": True},
        "child": {
            "name": "child",
            "content": "c",
            "extends": "skill:base_skill",
            "task_lock": False,
        },
    }
    out = inherit_extends_task_lock(bundles)
    assert out["child"]["task_lock"] is True


def test_inherit_extends_task_lock_no_extends_unchanged():
    bundles = {"plain": {"name": "plain", "content": "p", "task_lock": False}}
    out = inherit_extends_task_lock(bundles)
    assert out["plain"]["task_lock"] is False


def test_compose_extended_sop_bodies_propagates_task_lock():
    """End-to-end: compose_extended_sop_bodies applies task-lock inheritance."""
    bundles = {
        "signup_interview": {
            "name": "signup_interview",
            "content": "Custom.",
            "extends": "action:jvagent/interview",
            "allowed_tools_add": [],
            "disabled_tools": [],
            "task_lock": False,
        }
    }
    out = compose_extended_sop_bodies(bundles)
    assert out["signup_interview"]["task_lock"] is True


def test_inherit_lock_companions_union_along_skill_chain():
    bundles = {
        "base_skill": {
            "name": "base_skill",
            "content": "b",
            "lock_companions": ["faq"],
        },
        "child": {
            "name": "child",
            "content": "c",
            "extends": "skill:base_skill",
            "lock_companions": ["find_tool"],
        },
    }
    out = inherit_extends_lock_companions(bundles)
    assert out["child"]["lock_companions"] == ["faq", "find_tool"]


def test_inherit_lock_companions_no_extends_unchanged():
    bundles = {"plain": {"name": "plain", "content": "p", "lock_companions": ["faq"]}}
    out = inherit_extends_lock_companions(bundles)
    assert out["plain"]["lock_companions"] == ["faq"]


def test_load_action_base_sop_body_honors_env_override(monkeypatch, tmp_path):
    root = _make_core_action_root(tmp_path / "core_actions", marker="ENV_OVERRIDE")
    monkeypatch.setenv(_CORE_ENV, str(root))
    reset_sop_extend_cache()

    body = load_action_base_sop_body("jvagent/interview")

    assert "ENV_OVERRIDE" in body


def test_env_override_cache_refreshes_on_path_change(monkeypatch, tmp_path):
    first = _make_core_action_root(tmp_path / "first_actions", marker="FIRST")
    second = _make_core_action_root(tmp_path / "second_actions", marker="SECOND")

    monkeypatch.setenv(_CORE_ENV, str(first))
    reset_sop_extend_cache()
    body_first = load_action_base_sop_body("jvagent/interview")
    assert "FIRST" in body_first

    monkeypatch.setenv(_CORE_ENV, str(second))
    body_second = load_action_base_sop_body("jvagent/interview")
    assert "SECOND" in body_second
