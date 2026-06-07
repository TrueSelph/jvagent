"""Tests for InterviewAction.resolve_locked_skill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.orchestrator.skills import SkillDoc

pytestmark = pytest.mark.asyncio


async def test_resolve_locked_skill_from_session():
    skill = SkillDoc(
        name="feedback_interview",
        description="d",
        body="b",
        locked_in=True,
        requires_actions=("InterviewAction",),
    )
    conversation = MagicMock()
    conversation.context = {
        "interview": InterviewSession(interview_type="feedback_interview").to_dict()
    }
    conversation.save = AsyncMock()

    visitor = MagicMock()
    visitor.conversation = conversation

    action = InterviewAction()
    doc = await action.resolve_locked_skill(visitor, [skill])
    assert doc is not None
    assert doc.name == "feedback_interview"
