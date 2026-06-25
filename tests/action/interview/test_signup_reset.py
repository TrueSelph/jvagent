"""Base interview__reset tool."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession, save_session
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_reset_returns_reply_directive_not_next_field_chain(
    signup_action,
):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="start over")
    await save_session(conv, session)

    action._clear_interview_session = AsyncMock()
    action._close_task = AsyncMock()
    action._handle_start = AsyncMock(return_value='{"ok": true}')
    action._handle_next_field = AsyncMock(
        return_value='{"next_field": {"key": "user_email", "prompt": "What is your email?"}}'
    )

    result = json.loads(await action._handle_reset(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "restarted"
    directive = result["response_directive"]
    assert "Tell the user or ask the user:" in directive
    assert "start over" in directive.lower()
    assert "What is your email?" not in directive
    assert result.get("next_tool") == "interview__next_field"
    action._handle_next_field.assert_not_awaited()
