"""prepare_locked_skill_turn — message evaluation observations on every turn."""

from __future__ import annotations

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
async def test_prepare_injects_evaluation_for_inline_name(interview_action):
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
    action._save_session = AsyncMock()

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert len(prep.observations) == 1
    assert prep.observations[0]["tool"] == "interview__message_evaluation"
    assert "user_name" in prep.observations[0]["observation"]
    assert "interview__set_field" in (prep.pending_directive or "")


@pytest.mark.asyncio
async def test_prepare_seeds_next_question_for_intent_only(interview_action):
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
    assert "full name" in prep.observations[0]["observation"].lower()


@pytest.mark.asyncio
async def test_prepare_seeds_next_question_when_utterance_empty(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="")
    await save_session(conv, session)
    action._save_session = AsyncMock()

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert prep.observations[0]["tool"] == "interview__next_question"


@pytest.mark.asyncio
async def test_prepare_injects_evaluation_for_direct_answer(interview_action):
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    session.context[CTX_QUESTION_PRESENTED] = "user_name"
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="Jane Doe")
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.observations[0]["tool"] == "interview__message_evaluation"
    assert "Jane Doe" in prep.observations[0]["observation"]


@pytest.mark.asyncio
async def test_prepare_cancel_uses_model_intent_routing(interview_action):
    """Cancel is not server-detected — prep runs evaluation then next_question."""
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="cancel")
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.runtime_ready is True
    assert len(prep.observations) == 1
    assert prep.observations[0]["tool"] == "interview__next_question"
    assert all(o.get("tool") != "interview__control_intent" for o in prep.observations)


@pytest.mark.asyncio
async def test_prepare_start_over_uses_model_intent_routing(interview_action):
    """Start-over is not server-detected — prep runs evaluation then next_question."""
    action, _spec = interview_action
    session = InterviewSession(interview_type="signup_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="start over")
    await save_session(conv, session)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)

    assert prep.observations[0]["tool"] == "interview__next_question"
    assert all(o.get("tool") != "interview__control_intent" for o in prep.observations)
