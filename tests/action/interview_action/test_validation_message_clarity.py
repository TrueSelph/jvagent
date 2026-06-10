"""Validation failure envelopes: per-field validator errors and first-failure stop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview_action.conftest import SIGNUP_INTERVIEW_SKILL_DIR


@pytest.fixture
def signup_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.mark.asyncio
async def test_invalid_name_returns_full_name_validator_error(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="my name is Eldon")

    result = json.loads(
        await action._handle_set_fields(fields={"user_name": "Eldon"}, visitor=visitor)
    )

    assert result["ok"] is False
    assert result["validator"] == "validate_full_name"
    assert "first and last name" in (result.get("error") or "").lower()
    assert "latest message" not in (result.get("error") or "").lower()


@pytest.mark.asyncio
async def test_invalid_slot_returns_available_times_guidance(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="free on Tuesdays at 9")

    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Tuesdays at 9"}, visitor=visitor
        )
    )

    assert result["ok"] is False
    assert result["validator"] == "validate_available_times"
    assert "available training times" in (result.get("error") or "").lower()
    assert "latest message" not in (result.get("error") or "").lower()


@pytest.mark.asyncio
async def test_batch_stops_at_first_validation_failure(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(
        utterance="my name is Eldon and I am free on Tuesdays at 9",
    )

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "user_name": "Eldon",
                "available_times": "Tuesdays at 9",
            },
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert result["field"] == "user_name"
    assert "first and last name" in (result.get("error") or "").lower()
    by_field = {r["field"]: r for r in result["results"]}
    assert by_field["user_name"]["ok"] is False
    # First failure stops the pipeline; later fields are not processed.
    assert "available_times" not in by_field
    assert "available_times" not in session.fields
    assert "response_directive" in result
    assert "first and last name" in result["response_directive"].lower()


@pytest.mark.asyncio
async def test_batch_failure_single_directive(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(
        utterance="my name is Eldon and I am free on Tuesdays at 9",
    )

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "user_name": "Eldon",
                "available_times": "Tuesdays at 9",
            },
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert "next_field" not in result
    assert "next_tool" not in result
    assert "response_directive" in result
    assert len([k for k in result if k == "response_directive"]) == 1
