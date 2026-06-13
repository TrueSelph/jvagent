"""Guard tests for thin-harness invariants (no server sub-flows)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession, save_session
from jvagent.action.interview.spec import load_interview_spec_from_skill
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


def _visitor_with_session(session: InterviewSession) -> SimpleNamespace:
    conv = MagicMock()
    conv.context = {"interview": session.to_dict()}
    conv.save = AsyncMock()
    return SimpleNamespace(conversation=conv)


@pytest.mark.asyncio
async def test_reset_does_not_call_next_field_internally(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="start over")
    await save_session(conv, session)

    action._handle_next_field = AsyncMock(
        return_value='{"next_field": {"key": "user_email"}}'
    )

    result = json.loads(await action._handle_reset(visitor=visitor))

    assert result["ok"] is True
    assert result.get("next_tool") == "interview__next_field"
    action._handle_next_field.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_status_has_no_next_field_payload(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    visitor = _visitor_with_session(session)
    result = json.loads(await action._handle_get_status(visitor=visitor))

    assert result["ok"] is True
    assert "next_field" not in result
    assert "next_questions" not in result


@pytest.mark.asyncio
async def test_validation_failure_no_next_field_embedded(signup_action):
    action, _spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    visitor = _visitor_with_session(session)

    result = json.loads(
        await action._handle_set_fields(fields={"user_name": "x"}, visitor=visitor)
    )

    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert "next_field" not in result
    assert "next_questions" not in result
    assert [e for e in result["results"] if not e.get("stored")][0]["error"]
