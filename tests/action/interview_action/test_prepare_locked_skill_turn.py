"""prepare_locked_skill_turn — minimal runtime gate (no server steering)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession, save_session
from jvagent.action.interview_action.interview_action import InterviewAction
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def interview_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_prepare_runtime_ready_when_session_active(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(
        conversation=conv,
        utterance="Hello my name is Eldon Marks. I'm here to sign up",
    )
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert not prep.observations
    assert not prep.pending_directive


@pytest.mark.asyncio
async def test_prepare_runtime_not_ready_without_session(interview_action):
    action, _spec = interview_action
    visitor = SimpleNamespace(conversation=None, utterance="Sign me up")

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is False
    assert not prep.observations


@pytest.mark.asyncio
async def test_prepare_no_steering_for_cancel_utterance(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="cancel")
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert not prep.observations
