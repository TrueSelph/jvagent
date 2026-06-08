"""Base interview__reset_interview tool."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession, save_session
from jvagent.action.interview_action.interview_action import InterviewAction

_SIGNUP_SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/actions/jvagent/interview_action/skills/signup_interview"
)


@pytest.fixture
def signup_action():
    action = InterviewAction(
        metadata={"agent_dir": str(_SIGNUP_SKILL_DIR.parent.parent)}
    )
    spec = load_interview_spec_from_skill(_SIGNUP_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_reset_interview_returns_reply_directive_not_next_question_chain(
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
    action._handle_next_question = AsyncMock(
        return_value='{"next_questions": [{"question": "What is your email?"}]}'
    )

    result = json.loads(await action._handle_reset_interview(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "restarted"
    directive = result["response_directive"]
    assert "Tell the user:" in directive
    assert "interview__next_question" not in directive
    assert "start over" in directive.lower()
    assert "What is your email?" in directive
