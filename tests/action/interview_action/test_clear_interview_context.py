"""Terminal interview paths clear conversation.context by default."""

from __future__ import annotations

import pytest

from jvagent.action.interview_action.session import (
    InterviewSession,
    clear_interview_context,
    save_session,
)


@pytest.mark.asyncio
async def test_clear_interview_context_removes_scratch_retains_new_user():
    from unittest.mock import AsyncMock

    session = InterviewSession(interview_type="signup_interview")
    conv = type("Conv", (), {})()
    conv.context = {
        "new_user": False,
        "signup_records": {"x": 1},
        "matched_training_times": ["Monday"],
    }
    conv.save = AsyncMock()
    await save_session(conv, session)

    clear_interview_context(conv, retain_keys=())

    assert "interview" not in conv.context
    assert "signup_records" not in conv.context
    assert "matched_training_times" not in conv.context
    assert conv.context == {"new_user": False}


@pytest.mark.asyncio
async def test_clear_interview_context_honors_retain_keys():
    conv = type("Conv", (), {"context": {}})()
    conv.context = {
        "new_user": True,
        "interview": {},
        "customer_id": "GEO100188",
        "user_is_onboarded": "completed",
    }

    clear_interview_context(conv, retain_keys=["customer_id", "user_is_onboarded"])

    assert conv.context == {
        "new_user": True,
        "customer_id": "GEO100188",
        "user_is_onboarded": "completed",
    }
