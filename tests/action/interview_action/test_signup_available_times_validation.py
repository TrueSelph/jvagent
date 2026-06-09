"""Signup interview: available_times slot validation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.interview_action import InterviewAction
from tests.action.interview_action.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture
def signup_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.mark.asyncio
async def test_set_field_rejects_monday_at_7(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="available_times", value="Monday at 7")
    )

    assert result["ok"] is False
    assert result["stored"] is False
    assert result["status"] == "validation_failed"
    assert result["valid"] is False
    assert result["validator"] == "validate_available_times"
    assert "available_times" not in session.fields


@pytest.mark.asyncio
async def test_set_field_rejects_invalid_slot_even_with_stale_context(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.context["matched_training_times"] = ["Monday 9:00 AM - 11:00 AM"]
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="available_times", value="Monday at 7")
    )

    assert result["ok"] is False
    assert result["stored"] is False
    assert result["status"] == "validation_failed"
    assert "available_times" not in session.fields
    assert "matched_training_times" not in session.context


@pytest.mark.asyncio
async def test_set_field_rejects_model_override_of_invalid_utterance(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="Monday at 7")

    result = json.loads(
        await action._handle_set_field(
            field="available_times",
            value="Monday 9:00 AM - 11:00 AM",
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert result["stored"] is False
    assert result["status"] == "validation_failed"
    assert result["validated_from"] == "rejected_ungrounded"
    assert "latest message" in (result.get("error") or "").lower()
    assert "available_times" not in session.fields


@pytest.mark.asyncio
async def test_set_field_accepts_monday_at_9_autocorrect(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Monday at 9")
    result = json.loads(
        await action._handle_set_field(
            field="available_times",
            value="Monday at 9",
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert result["stored"] is True
    assert session.get_value("available_times") == "Monday 9:00 AM - 11:00 AM"
