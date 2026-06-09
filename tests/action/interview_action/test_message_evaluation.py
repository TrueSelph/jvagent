"""Per-message entity evaluation — surfacing candidates for model extraction."""

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
from jvagent.action.interview_action.runtime.message_evaluation import (
    evaluate_message_for_extraction,
)
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)

_PRE_ALERT_SKILL_DIR = (
    __import__("pathlib").Path(__file__).resolve().parent
    / "fixtures/skills/pre_alert_interview"
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.fixture
def pre_alert_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(_PRE_ALERT_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_evaluation_surfaces_intro_name(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    visitor = SimpleNamespace(conversation=None)

    result = await evaluate_message_for_extraction(
        action,
        session,
        spec,
        "Hello my name is Eldon Marks. I'm here to sign up",
        visitor,
    )

    assert result.applicable
    assert result.applicable[0].field == "user_name"
    assert "Eldon Marks" in result.applicable[0].candidates


@pytest.mark.asyncio
async def test_evaluation_intent_only_empty_applicable(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")

    result = await evaluate_message_for_extraction(
        action,
        session,
        spec,
        "Sign me up for training",
        None,
    )

    assert result.applicable == []
    assert "user_name" in result.missing_required


@pytest.mark.asyncio
async def test_evaluation_surfaces_tracking_and_email(pre_alert_action):
    action, spec = pre_alert_action
    session = InterviewSession(interview_type="pre_alert_interview")

    result = await evaluate_message_for_extraction(
        action,
        session,
        spec,
        "Track 291421515335 for jane@example.com please",
        None,
    )

    fields = {h.field for h in result.applicable}
    assert "tracking_number" in fields


@pytest.mark.asyncio
async def test_evaluation_short_direct_answer_with_question_presented(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.context[CTX_QUESTION_PRESENTED] = "user_name"

    result = await evaluate_message_for_extraction(
        action,
        session,
        spec,
        "Jane Doe",
        None,
    )

    assert result.applicable
    assert result.applicable[0].field == "user_name"
    assert "Jane Doe" in result.applicable[0].candidates


@pytest.mark.asyncio
async def test_evaluation_cancel_not_applicable(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.context[CTX_QUESTION_PRESENTED] = "user_name"

    result = await evaluate_message_for_extraction(
        action,
        session,
        spec,
        "cancel",
        None,
    )

    assert result.applicable == []
    assert result.no_match_reason == "no_valid_candidates_for_missing_fields"


@pytest.mark.asyncio
async def test_start_does_not_auto_store_tracking(pre_alert_action):
    action, spec = pre_alert_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    result = __import__("json").loads(
        await action._handle_start(
            "pre_alert_interview",
            user_message="Please track my package 291421515335",
        )
    )

    assert result["ok"] is True
    assert "tracking_number" not in result.get("fields", {})
    assert "seeded_fields" not in result
