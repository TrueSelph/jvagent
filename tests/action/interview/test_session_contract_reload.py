"""Regression: interview session in context must survive spec reload on later turns."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession, save_session


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

    # _get_session_and_contract lazy-reloads specs before serving the session.
    session, spec = await action._get_session_and_contract(visitor)
    assert session is not None
    assert spec is not None
    assert spec.name == "onboarding_interview"


@pytest.mark.asyncio
async def test_get_session_and_contract_returns_no_spec_when_unavailable(tmp_path):
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

    # Session is in context, but its spec cannot be loaded (no skill dir) — the
    # session is returned with a None spec; handlers fall back to NO_SESSION.
    session, spec = await action._get_session_and_contract(visitor)
    assert session is not None
    assert spec is None


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

    mock_discover.assert_not_called()
