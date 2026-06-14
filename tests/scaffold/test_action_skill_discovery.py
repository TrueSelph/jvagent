"""Tests for action-backed skill discovery (ADR-0020 placement convention)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.scaffold.skill_resolve import (
    resolve_agent_action_skills,
    resolve_agent_skills,
    resolve_core_action_skills,
    resolve_merged_skill_bundles,
)
from jvagent.scaffold.sop_extend import reset_sop_extend_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_sop_extend_cache()
    yield
    reset_sop_extend_cache()


def test_resolve_core_action_skills_skips_examples_dir():
    """Reference packages under interview/examples/ are not discovered."""
    refs = ["jvagent/interview", "jvagent/orchestrator"]
    found = resolve_core_action_skills(refs)
    assert "example_interview" not in found


def test_resolve_core_action_skills_skips_non_jvagent_refs():
    found = resolve_core_action_skills(["custom/my_action"])
    assert found == {}


def test_resolve_agent_action_skills_signup_overlay(tmp_path: Path):
    skill_dir = (
        tmp_path
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "actions"
        / "jvagent"
        / "interview"
        / "skills"
        / "signup_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: signup_interview\ndescription: signup test\n"
        "requires-actions:\n  - InterviewAction\n"
        "extends: action:jvagent/interview\n---\n\nCustom.",
        encoding="utf-8",
    )
    found = resolve_agent_action_skills(
        str(tmp_path),
        "jvagent",
        "orchestrator_agent",
        action_refs=["jvagent/interview"],
    )
    assert "signup_interview" in found
    assert found["signup_interview"]["source"] == "app"


def test_merged_bundles_includes_action_skill_with_extends(tmp_path: Path):
    skill_dir = (
        tmp_path
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "actions"
        / "jvagent"
        / "interview"
        / "skills"
        / "signup_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: signup_interview\ndescription: signup\n"
        "requires-actions:\n  - InterviewAction\n"
        "extends: action:jvagent/interview\n---\n\nBe friendly.",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agents/jvagent/orchestrator_agent/agent.yaml"
    agent_yaml.parent.mkdir(parents=True, exist_ok=True)
    agent_yaml.write_text(
        "actions:\n  - action: jvagent/interview\n",
        encoding="utf-8",
    )
    merged = resolve_merged_skill_bundles(
        str(tmp_path),
        "jvagent",
        "orchestrator_agent",
        include_builtin=False,
    )
    assert "signup_interview" in merged
    assert "Standard Interview Procedure" in merged["signup_interview"]["content"]
    assert "Be friendly." in merged["signup_interview"]["content"]


def test_agent_skills_accepts_requires_actions(tmp_path: Path, caplog):
    """requires-actions in agents/.../skills/ is valid app-local placement."""
    skill_dir = tmp_path / "agents" / "jv" / "bot" / "skills" / "web_lookup"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: web_lookup\ndescription: lookup\n"
        "requires-actions:\n  - SerperWebSearchAction\n---\n\nbody",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        found = resolve_agent_skills(str(tmp_path), "jv", "bot")

    assert "web_lookup" in found
    assert found["web_lookup"]["requires_actions"] == ["SerperWebSearchAction"]
    assert not any("agents/jv/bot/skills/" in r.message for r in caplog.records)
