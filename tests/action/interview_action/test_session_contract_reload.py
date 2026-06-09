"""Regression: interview session in context must survive spec reload on later turns."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview_action.core.session import InterviewSession, save_session
from jvagent.action.interview_action.interview_action import InterviewAction


@pytest.mark.asyncio
async def test_set_field_works_after_registry_cleared(tmp_path):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: onboarding_interview
description: Onboarding
interview:
  title: Onboarding
  fields:
    - key: phone_number
      prompt: What is your phone number?
      required: true
      validator: phone
---
""",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }
    await action._discover_specs()

    conversation = MagicMock()
    conversation.context = {}
    conversation.save = AsyncMock()
    session = InterviewSession(interview_type="onboarding_interview")
    await save_session(conversation, session)

    visitor = MagicMock()
    visitor.conversation = conversation

    # Simulate a fresh action instance / cleared in-memory registry (turn 2).
    action._registry._specs.clear()
    # _interview_ready lazy-reloads specs before checking session.
    assert await action._interview_ready(visitor) is True

    session, spec = await action._get_session_and_contract(visitor)
    assert session is not None
    assert spec is not None
    assert spec.name == "onboarding_interview"


@pytest.mark.asyncio
async def test_interview_ready_false_when_session_without_spec(tmp_path):
    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }

    conversation = MagicMock()
    conversation.context = {
        "interview": {
            "interview_type": "onboarding_interview",
            "status": "active",
            "fields": {},
            "skipped_fields": [],
        }
    }
    visitor = MagicMock()
    visitor.conversation = conversation

    assert await action._interview_ready(visitor) is False


@pytest.mark.asyncio
async def test_ensure_specs_loaded_skips_when_registry_populated(tmp_path):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: onboarding_interview
description: Onboarding
interview:
  title: Onboarding
  fields:
    - key: phone_number
      prompt: What is your phone number?
      required: true
---
""",
        encoding="utf-8",
    )

    action = InterviewAction()
    action.metadata = {
        "agent_namespace": "zoon",
        "agent_name": "zoon_ai",
        "agent_dir": str(tmp_path / "agents" / "zoon" / "zoon_ai"),
    }
    await action._discover_specs()

    with patch.object(
        action, "_discover_specs", new_callable=AsyncMock
    ) as mock_discover:
        await action._ensure_specs_loaded()
        await action._ensure_specs_loaded()
        await action._interview_ready(MagicMock())

    mock_discover.assert_not_called()
