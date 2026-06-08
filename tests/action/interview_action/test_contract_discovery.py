"""InterviewAction must discover interview specs from the hosting agent's skills/."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction


@pytest.mark.asyncio
async def test_resolve_skills_dirs_uses_app_root_and_agent_metadata(tmp_path):
    skills = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: onboarding_interview\ninterview:\n  title: Onboarding\n  questions: []\n---\n",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        dirs = await action._resolve_skills_dirs()

    assert dirs == [str(tmp_path / "agents" / "zoon" / "zoon_ai" / "skills")]


@pytest.mark.asyncio
async def test_resolve_skills_dirs_falls_back_to_agent_node(tmp_path, monkeypatch):
    skills = tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "pre_alert_interview"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: pre_alert_interview\ninterview:\n  title: Pre-alert\n  questions: []\n---\n",
        encoding="utf-8",
    )

    agent = MagicMock()
    agent.namespace = "zoon"
    agent.name = "zoon_ai"

    action = InterviewAction()
    action.metadata = {"agent_name": "zoon_ai"}
    monkeypatch.setattr(
        InterviewAction,
        "get_agent",
        AsyncMock(return_value=agent),
    )

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        dirs = await action._resolve_skills_dirs()

    assert len(dirs) == 1
    assert dirs[0].endswith("agents/zoon/zoon_ai/skills")


@pytest.mark.asyncio
async def test_discover_specs_loads_agent_interview_skills(tmp_path):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: onboarding_interview\ninterview:\n  title: Onboarding\n  questions: []\n---\n",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        await action._discover_specs()

    assert action.is_interview_skill("onboarding_interview")
    assert "onboarding_interview" in action._registry.list_specs()


@pytest.mark.asyncio
async def test_resolve_skills_dirs_includes_action_overlay(tmp_path):
    overlay = (
        tmp_path
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "actions"
        / "jvagent"
        / "interview_action"
        / "skills"
        / "signup_interview"
    )
    overlay.mkdir(parents=True)
    (overlay / "SKILL.md").write_text(
        "---\nname: signup_interview\ndescription: signup\n"
        "requires-actions:\n  - InterviewAction\n"
        "interview:\n  title: Signup\n  description: signup\n"
        "  questions: []\n  completion:\n    function: noop\n---\n\nbody",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "namespace": "jvagent",
        "name": "interview_action",
        "agent_namespace": "jvagent",
        "agent_name": "orchestrator_agent",
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        dirs = await action._resolve_skills_dirs()

    expected = str(
        tmp_path
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "actions"
        / "jvagent"
        / "interview_action"
        / "skills"
    )
    assert expected in dirs


@pytest.mark.asyncio
async def test_discover_specs_loads_action_overlay_signup(tmp_path):
    overlay = (
        tmp_path
        / "agents"
        / "jvagent"
        / "orchestrator_agent"
        / "actions"
        / "jvagent"
        / "interview_action"
        / "skills"
        / "signup_interview"
    )
    overlay.mkdir(parents=True)
    (overlay / "SKILL.md").write_text(
        "---\nname: signup_interview\ndescription: signup\n"
        "requires-actions:\n  - InterviewAction\n"
        "interview:\n  title: Signup\n  description: signup\n"
        "  questions: []\n  completion:\n    function: noop\n---\n\nbody",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "namespace": "jvagent",
        "name": "interview_action",
        "agent_namespace": "jvagent",
        "agent_name": "orchestrator_agent",
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        await action._discover_specs()

    assert action.is_interview_skill("signup_interview")


@pytest.mark.asyncio
async def test_discover_specs_loads_jvagent_app_signup_overlay():
    """Regression: signup_interview lives under action overlay, not agent skills/."""
    app_root = Path(__file__).resolve().parents[3] / "examples" / "jvagent_app"
    action = InterviewAction()
    action.metadata = {
        "namespace": "jvagent",
        "name": "interview_action",
        "agent_namespace": "jvagent",
        "agent_name": "orchestrator_agent",
        "agent_dir": str(app_root / "agents" / "jvagent" / "orchestrator_agent"),
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(app_root)):
        await action._discover_specs()

    assert action.is_interview_skill("signup_interview")
    spec = action._registry.get("signup_interview")
    assert spec is not None
    assert "user_name" in [q.name for q in spec.questions]


@pytest.mark.asyncio
async def test_does_not_walk_up_to_jvagent_skills_library(monkeypatch):
    action = InterviewAction()
    action.metadata = {}

    with patch("jvagent.core.app_context.get_app_root", return_value=None):
        monkeypatch.setattr(
            InterviewAction,
            "get_agent",
            AsyncMock(return_value=None),
        )
        dirs = await action._resolve_skills_dirs()

    assert dirs == []
