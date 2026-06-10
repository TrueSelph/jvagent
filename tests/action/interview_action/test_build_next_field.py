"""build_next_field includes field guidance for model-facing acceptance criteria."""

from __future__ import annotations

import pytest

from jvagent.action.interview_action.flow import build_next_field
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview_action.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.mark.asyncio
async def test_build_next_field_includes_guidance():
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    session = InterviewSession(interview_type="signup_interview")

    next_field = await build_next_field(session, spec, lambda _name: None)

    assert next_field is not None
    assert next_field["key"] == "user_name"
    guidance = next_field.get("guidance") or next_field.get("description", "")
    assert "acknowledgement" in guidance.lower()
