"""tell_user_then_continue — branch-aware post-processor directives."""

from __future__ import annotations

import pytest

from jvagent.action.interview.hooks import hook_execution_context
from jvagent.action.interview.responses import tell_user_then_continue
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture
def signup_spec():
    return load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)


@pytest.mark.asyncio
async def test_requires_hook_execution_context():
    with pytest.raises(RuntimeError, match="must be called from an interview hook"):
        await tell_user_then_continue("Thanks.")


@pytest.mark.asyncio
async def test_work_email_next_question_is_employer(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@mail.com")

    with hook_execution_context(session=session, spec=signup_spec):
        directive = await tell_user_then_continue("Thanks for your work email.")

    assert "work email" in directive.lower() or "Thanks" in directive
    assert "company" in directive.lower() or "organization" in directive.lower()
    assert "phone" not in directive.lower()


@pytest.mark.asyncio
async def test_no_next_question_chains_review(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@gmail.com")
    session.set_value("phone_number", "5551234567")

    with hook_execution_context(session=session, spec=signup_spec):
        directive = await tell_user_then_continue("All set on our side.")

    assert "all set on our side" in directive.lower()
    assert "interview__review" in directive
    assert "Then ask:" not in directive


@pytest.mark.asyncio
async def test_personal_email_next_question_is_phone(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@gmail.com")

    with hook_execution_context(session=session, spec=signup_spec):
        directive = await tell_user_then_continue("Thanks.")

    assert "phone" in directive.lower()
