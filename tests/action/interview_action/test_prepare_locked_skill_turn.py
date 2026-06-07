"""prepare_locked_skill_turn — avoid redundant next_question on signup/answer turns."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import (
    CTX_QUESTION_PRESENTED,
    InterviewSession,
    save_session,
)
from jvagent.action.interview_action.interview_action import InterviewAction

_SIGNUP_SKILL_DIR = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/actions/jvagent/interview_action/skills/signup_interview"
)


@pytest.fixture
def interview_action():
    action = InterviewAction(
        metadata={"agent_dir": str(_SIGNUP_SKILL_DIR.parent.parent)}
    )
    spec = load_interview_spec_from_skill(_SIGNUP_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_should_seed_when_utterance_fails_pending_field_validation(
    interview_action,
):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(
        conversation=conv,
        utterance="Sign me up for training",
    )
    await save_session(conv, session)

    should_seed, pending = await action._should_seed_next_question(
        "signup_interview", visitor
    )
    assert should_seed is True
    assert pending == "user_name"


@pytest.mark.asyncio
async def test_should_not_seed_when_user_is_answering(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    session.context[CTX_QUESTION_PRESENTED] = "user_name"
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="Jane Doe")
    await save_session(conv, session)

    should_seed, pending = await action._should_seed_next_question(
        "signup_interview", visitor
    )
    assert should_seed is False
    assert pending == "user_name"


@pytest.mark.asyncio
async def test_prepare_seeds_once_when_utterance_is_not_field_answer(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(
        conversation=conv,
        utterance="Sign me up for training",
    )
    await save_session(conv, session)
    action._save_session = AsyncMock()

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert len(prep.observations) == 1
    assert prep.observations[0]["tool"] == "interview__next_question"
    assert "Do NOT call interview__next_question again" in (
        prep.pending_directive or ""
    )
    assert "full name" in prep.observations[0]["observation"].lower()


@pytest.mark.asyncio
async def test_prepare_directs_set_field_on_answer_turn(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    session.context[CTX_QUESTION_PRESENTED] = "user_name"
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="Jane Doe")
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert prep.observations == []
    assert "interview__set_field" in (prep.pending_directive or "")
    assert "Do NOT call interview__next_question" in (prep.pending_directive or "")
