"""set_fields must ground values in the user's latest message (thin harness)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import (
    CTX_FIELD_SUGGESTION,
    InterviewSession,
)
from jvagent.action.interview_action.interview_action import InterviewAction
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_rejects_ungrounded_value_when_utterance_present(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="yes")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "stale@example.com"},
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert result["validated_from"] == "rejected_ungrounded"
    assert "user_email" not in session.fields or session.get_value("user_email") is None


@pytest.mark.asyncio
async def test_accepts_suggested_value_on_confirm(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@example.com")
    session.context[CTX_FIELD_SUGGESTION] = {
        "field": "phone_number",
        "value": "5912345678",
    }
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="yes")
    result = json.loads(
        await action._handle_set_fields(
            fields={"phone_number": "5912345678"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("phone_number") == "5912345678"
    assert result["validated_from"] == "suggested"
    assert CTX_FIELD_SUGGESTION not in session.context


@pytest.mark.asyncio
async def test_grounded_correction_still_stores(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="change my email to eldon@mail.com")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "eldon@mail.com"
    assert result["validated_from"] in ("utterance", "supplied_grounded")
