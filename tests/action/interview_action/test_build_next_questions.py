"""build_next_questions includes field guidance for model-facing acceptance criteria."""

from __future__ import annotations

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.runtime.path_resolver import build_next_questions
from tests.action.interview_action.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.mark.asyncio
async def test_build_next_questions_includes_guidance():
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    session = InterviewSession(interview_type="signup_interview")

    next_qs = await build_next_questions(session, spec, lambda _name: None)

    assert len(next_qs) == 1
    assert next_qs[0]["key"] == "user_name"
    guidance = next_qs[0].get("guidance") or next_qs[0].get("description", "")
    assert "acknowledgement" in guidance.lower()
