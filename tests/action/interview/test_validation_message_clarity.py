"""Validation failure envelopes: per-field validator errors in batch mode."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


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
    failed = [e for e in result["results"] if not e.get("stored")]
    assert "first and last name" in (failed[0].get("error") or "").lower()
    assert "latest message" not in (failed[0].get("error") or "").lower()


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
    failed = [e for e in result["results"] if not e.get("stored")]
    assert "available training times" in (failed[0].get("error") or "").lower()
    assert "latest message" not in (failed[0].get("error") or "").lower()


@pytest.mark.asyncio
async def test_batch_reports_all_validation_failures(signup_action):
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
    by_field = {r["field"]: r for r in result["results"]}
    assert by_field["user_name"]["stored"] is False
    assert "first and last name" in (by_field["user_name"].get("error") or "").lower()
    assert by_field["available_times"]["stored"] is False
    assert "available_times" not in session.fields
    failed = [e for e in result["results"] if not e.get("stored")]
    assert len(failed) == 2
    assert "response_directive" in result
    assert "corrected values" in result["response_directive"].lower()


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
