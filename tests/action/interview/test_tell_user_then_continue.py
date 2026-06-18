"""ctx.say(continue_=True) — branch-aware sidebar-then-next-prompt directives."""

from __future__ import annotations

import pytest

from jvagent.action.interview.hooks import POST_PHASE, call_hook
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture
def signup_spec():
    return load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)


def _make_hook(sidebar: str):
    async def hook(ctx):
        ctx.say(sidebar, continue_=True)
        return ctx.tool_response(ok=True, status="ok")

    return hook


async def _continue_directive(session, spec, sidebar):
    result = await call_hook(
        _make_hook(sidebar), session=session, spec=spec, phase=POST_PHASE
    )
    return result["response_directive"]


@pytest.mark.asyncio
async def test_work_email_next_question_is_employer(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@mail.com")

    directive = await _continue_directive(
        session, signup_spec, "Thanks for your work email."
    )

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

    directive = await _continue_directive(session, signup_spec, "All set on our side.")

    assert "all set on our side" in directive.lower()
    assert "interview__review" in directive


@pytest.mark.asyncio
async def test_personal_email_next_question_is_phone(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@gmail.com")

    directive = await _continue_directive(session, signup_spec, "Thanks.")

    assert "phone" in directive.lower()
