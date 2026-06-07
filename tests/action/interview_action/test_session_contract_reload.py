"""Regression: interview session in context must survive contract reload on later turns."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession, save_session


@pytest.mark.asyncio
async def test_set_field_works_after_registry_cleared(tmp_path):
    skill_dir = (
        tmp_path / "agents" / "zoon" / "zoon_ai" / "skills" / "onboarding_interview"
    )
    skill_dir.mkdir(parents=True)
    (skill_dir / "interview.yaml").write_text(
        "name: onboarding_interview\n"
        "questions:\n"
        "  - name: phone_number\n"
        "    question: What is your phone number?\n"
        "    required: true\n"
        "    type: text\n"
        "    validators: [phone_gy]\n",
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
    # _interview_ready lazy-reloads contracts before checking session.
    assert await action._interview_ready(visitor) is True

    session, contract = await action._get_session_and_contract(visitor)
    assert session is not None
    assert contract is not None
    assert contract.name == "onboarding_interview"


@pytest.mark.asyncio
async def test_interview_ready_false_when_session_without_contract(tmp_path):
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
