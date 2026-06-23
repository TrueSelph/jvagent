"""``activation_utterance`` session context stashed by ``handle_start``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview.engine import handle_start
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import (
    ACTIVATION_UTTERANCE_KEY,
    InterviewSession,
    InterviewStatus,
    load_session,
    save_session,
)
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)

_ACTIVATION = "Hello my name is Eldon Marks. I'm here to sign up"
_RESUME = "I want to finish signing up please"


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action


def _visitor_with_conv() -> tuple[SimpleNamespace, MagicMock]:
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv)
    return visitor, conv


@pytest.mark.asyncio
@patch("jvagent.action.interview.engine.tasks.ensure_active_task", new_callable=AsyncMock)
async def test_fresh_start_stashes_activation_utterance(mock_task, signup_action):
    visitor, conv = _visitor_with_conv()
    signup_action._get_conversation = AsyncMock(return_value=conv)

    await handle_start(
        signup_action,
        "signup_interview",
        visitor,
        user_message=_ACTIVATION,
    )

    session = load_session(conv)
    assert session is not None
    assert session.context[ACTIVATION_UTTERANCE_KEY] == _ACTIVATION
    mock_task.assert_awaited_once()


@pytest.mark.asyncio
@patch("jvagent.action.interview.engine.tasks.ensure_active_task", new_callable=AsyncMock)
async def test_resume_empty_fields_updates_activation_utterance(mock_task, signup_action):
    visitor, conv = _visitor_with_conv()
    session = InterviewSession(
        interview_type="signup_interview",
        status=InterviewStatus.ACTIVE,
        context={ACTIVATION_UTTERANCE_KEY: "old utterance"},
    )
    await save_session(conv, session)
    signup_action._get_conversation = AsyncMock(return_value=conv)

    await handle_start(
        signup_action,
        "signup_interview",
        visitor,
        user_message=_RESUME,
    )

    reloaded = load_session(conv)
    assert reloaded is not None
    assert reloaded.context[ACTIVATION_UTTERANCE_KEY] == _RESUME
    mock_task.assert_awaited_once()


@pytest.mark.asyncio
@patch("jvagent.action.interview.engine.tasks.ensure_active_task", new_callable=AsyncMock)
async def test_resume_with_fields_does_not_clobber_activation_utterance(
    mock_task, signup_action
):
    visitor, conv = _visitor_with_conv()
    session = InterviewSession(
        interview_type="signup_interview",
        status=InterviewStatus.ACTIVE,
        fields={"user_name": "Eldon Marks"},
        context={ACTIVATION_UTTERANCE_KEY: _ACTIVATION},
    )
    await save_session(conv, session)
    signup_action._get_conversation = AsyncMock(return_value=conv)

    await handle_start(
        signup_action,
        "signup_interview",
        visitor,
        user_message=_RESUME,
    )

    reloaded = load_session(conv)
    assert reloaded is not None
    assert reloaded.context[ACTIVATION_UTTERANCE_KEY] == _ACTIVATION
    mock_task.assert_awaited_once()
