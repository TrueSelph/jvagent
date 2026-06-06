"""InterviewAction must discover contract.yaml from the hosting agent's skills/."""

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
    (skills / "contract.yaml").write_text(
        "name: onboarding_interview\nquestions: []\n",
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
    (skills / "contract.yaml").write_text(
        "name: pre_alert_interview\nquestions: []\n",
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
async def test_discover_contracts_loads_agent_interview_skills(tmp_path):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "contract.yaml").write_text(
        "name: onboarding_interview\nquestions: []\n",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
    }

    with patch("jvagent.core.app_context.get_app_root", return_value=str(tmp_path)):
        await action._discover_contracts()

    assert action.is_interview_skill("onboarding_interview")
    assert "onboarding_interview" in action._contract_registry.list_contracts()


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
