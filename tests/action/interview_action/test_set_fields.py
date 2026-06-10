"""Batch set_fields / get_fields and correction paths."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import (
    InterviewSession,
    InterviewStatus,
)
from jvagent.action.interview_action.spec import (
    load_interview_spec_from_skill,
)
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
async def test_set_fields_batch_stores_multiple(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Jane Doe and jane@example.com")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["field"] == "user_name"
    assert session.get_value("user_name") == "Jane Doe"


@pytest.mark.asyncio
async def test_set_fields_correction_mid_active(signup_action):
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


@pytest.mark.asyncio
async def test_get_status_returns_collected_fields(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_status(visitor=SimpleNamespace()))

    assert result["ok"] is True
    assert result["fields"]["user_name"] == "Jane Doe"
    assert "next_field" not in result


@pytest.mark.asyncio
async def test_review_email_correction_via_set_fields(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.status = InterviewStatus.REVIEW
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon.marks@gmail.com")
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


def test_set_fields_tool_schema_requires_fields_wrapper(signup_action):
    action, _spec = signup_action
    from jvagent.action.interview_action.tools import build_tools

    tool = next(t for t in build_tools(action) if t.name == "interview__set_fields")
    schema = tool.parameters_schema
    assert schema.get("required") == ["fields"]
    assert schema.get("additionalProperties") is False
    assert "fields" in schema.get("properties", {})


def test_normalize_field_map_requires_fields_wrapper(signup_action):
    action, _spec = signup_action
    mapped = action._normalize_field_map({"user_name": "Jane Doe"})
    assert mapped == {"user_name": "Jane Doe"}
    assert action._normalize_field_map(None) == {}
