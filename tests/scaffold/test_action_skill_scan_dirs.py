"""Tests for shared action-backed skill scan path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.scaffold.skill_resolve import (
    action_overlay_skills_dir,
    action_ref_from_metadata,
    resolve_action_skill_scan_dirs,
)


def test_action_ref_from_metadata_uses_info_yaml_fields():
    assert action_ref_from_metadata({"namespace": "jvagent", "name": "interview"}) == (
        "jvagent/interview"
    )
    assert action_ref_from_metadata({}) is None


def test_action_overlay_skills_dir(tmp_path: Path):
    overlay = (
        tmp_path / "agents" / "jv" / "bot" / "actions" / "acme" / "my_action" / "skills"
    )
    overlay.mkdir(parents=True)
    assert action_overlay_skills_dir(
        tmp_path / "agents" / "jv" / "bot", "acme/my_action"
    ) == str(overlay)


def test_resolve_action_skill_scan_dirs_overlay_and_legacy(tmp_path: Path):
    agent_base = tmp_path / "agents" / "jvagent" / "orchestrator_agent"
    overlay = agent_base / "actions" / "jvagent" / "interview" / "skills" / "signup"
    legacy = agent_base / "skills" / "old_skill"
    overlay.mkdir(parents=True)
    legacy.mkdir(parents=True)

    meta = {
        "namespace": "jvagent",
        "name": "interview",
        "agent_dir": str(agent_base),
    }
    dirs = resolve_action_skill_scan_dirs(meta)
    assert dirs == [
        str(agent_base / "actions" / "jvagent" / "interview" / "skills"),
        str(agent_base / "skills"),
    ]


@pytest.mark.asyncio
async def test_interview_uses_base_resolve_skill_scan_dirs(tmp_path: Path):
    agent_base = tmp_path / "agents" / "jvagent" / "orchestrator_agent"
    overlay = (
        agent_base / "actions" / "jvagent" / "interview" / "skills" / "signup_interview"
    )
    overlay.mkdir(parents=True)
    (overlay / "SKILL.md").write_text(
        "---\nname: signup_interview\ndescription: x\n"
        "interview:\n  title: T\n  description: d\n  questions: []\n"
        "  completion:\n    function: f\n---\n",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "namespace": "jvagent",
        "name": "interview",
        "agent_dir": str(agent_base),
    }
    dirs = await action.resolve_skill_scan_dirs()
    assert str(agent_base / "actions" / "jvagent" / "interview" / "skills") in dirs
    assert action.get_action_ref() == "jvagent/interview"
