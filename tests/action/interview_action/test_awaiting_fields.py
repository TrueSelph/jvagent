"""awaiting_fields envelope — branch-aware context for set_fields key mapping."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.flow import build_awaiting_fields
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import load_interview_spec_from_skill
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
async def test_activation_includes_awaiting_fields_not_field_definitions(signup_action):
    action, _spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    result = json.loads(
        await action._handle_start("signup_interview", visitor, user_message="sign up")
    )

    assert result["ok"] is True
    assert "awaiting_fields" in result
    assert result["awaiting_fields"][0]["key"] == "user_name"
    assert "user_name" in result["field_awareness"]
    assert "field_definitions" not in result


@pytest.mark.asyncio
async def test_on_skill_activate_includes_awaiting_fields(signup_action):
    action, _spec = signup_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    note = await action.on_skill_activate("signup_interview", user_message="sign up")
    assert note.startswith("Awaiting user input for 'user_name' field.")
    _awareness, json_body = note.split("\n\n", 1)
    parsed = json.loads(json_body)

    assert parsed["awaiting_fields"][0]["key"] == "user_name"
    assert "user_name" in parsed["field_awareness"]
    assert "field_definitions" not in parsed


@pytest.mark.asyncio
async def test_unknown_field_references_awaiting_keys_only(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(
        await action._handle_set_fields(
            fields={"full_name": "Eldon Marks"},
            visitor=SimpleNamespace(),
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "UNKNOWN_FIELD"
    assert "user_name" in result["error"]
    assert "employer_name" not in result["error"]
    assert "training_format" not in result["error"]
    assert result["awaiting_fields"][0]["key"] == "user_name"
    assert "user_name" in result["field_awareness"]
    assert "next_field" not in result
    assert "system_message" in result
    assert "user_name" in result["system_message"]


@pytest.mark.asyncio
async def test_set_fields_success_includes_awaiting_fields(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=SimpleNamespace(),
        )
    )

    assert result["ok"] is True
    assert result["awaiting_fields"][0]["key"] == "available_times"


@pytest.mark.asyncio
async def test_saturday_branch_awaiting_includes_training_format(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Saturday 9:00 AM - 11:00 AM")

    awaiting = await build_awaiting_fields(session, spec, lambda _n: None)
    keys = [f["key"] for f in awaiting]

    assert "training_format" in keys
    assert "employer_name" not in keys
    assert "user_email" not in keys


@pytest.mark.asyncio
async def test_get_status_has_field_definitions_and_awaiting_fields(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_status(visitor=SimpleNamespace()))

    assert result["awaiting_fields"][0]["key"] == "user_name"
    assert "field_definitions" in result
    assert len(result["field_definitions"]) == len(spec.fields)
