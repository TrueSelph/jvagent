"""Custom reset handler via interview.reset frontmatter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession, save_session
from jvagent.action.interview_action.spec import (
    load_interview_spec_from_skill,
)

_ONBOARDING = Path(__file__).resolve().parent / "fixtures/skills/onboarding_interview"


@pytest.fixture
def onboarding_action():
    action = InterviewAction(
        metadata={"agent_dir": str(_ONBOARDING.parent.parent.parent)}
    )
    spec = load_interview_spec_from_skill(_ONBOARDING)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_reset_delegates_to_custom_handler(onboarding_action):
    action, spec = onboarding_action
    assert spec.handlers.reset == "reset_onboarding"

    session = InterviewSession(interview_type="onboarding_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="cancel")
    await save_session(conv, session)

    action._close_task = AsyncMock()

    result = json.loads(await action._handle_reset(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert "Tell the user:" in result["response_directive"]
    assert "onboarding" in result["response_directive"].lower()
    action._close_task.assert_called_once()
