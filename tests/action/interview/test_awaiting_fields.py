"""awaiting_fields envelope — branch-aware context for set_fields key mapping."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.flow import build_awaiting_fields
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
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
    # Single field catalog; no redundant context duplicating it.
    assert result["field_reference"][0]["key"] == "user_name"
    assert result["start_field"] == "user_name"
    for gone in (
        "awaiting_fields",
        "field_keys",
        "required_keys",
        "active_path_keys",
        "guidance_page",
        "field_definitions",
    ):
        assert gone not in result


@pytest.mark.asyncio
async def test_on_skill_activate_includes_awaiting_fields(signup_action):
    action, _spec = signup_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    note = await action.on_skill_activate("signup_interview", user_message="sign up")
    parsed = json.loads(note)

    assert parsed["field_reference"][0]["key"] == "user_name"
    assert parsed["start_field"] == "user_name"
    for gone in ("awaiting_fields", "field_keys", "guidance_page", "field_definitions"):
        assert gone not in parsed


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
    failed = [e for e in result["results"] if not e.get("stored")]
    assert "user_name" in failed[0]["error"]
    assert "employer_name" not in failed[0]["error"]
    assert "training_format" not in failed[0]["error"]
    assert "awaiting_fields" not in result
    assert "next_field" not in result
    assert "system_message" in result
    assert "user_name" in result["system_message"]


@pytest.mark.asyncio
async def test_set_fields_success_is_compact(signup_action):
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
    assert "awaiting_fields" not in result
    assert "guidance_page" not in result
    assert result.get("next_tool") == "interview__next_field"


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
async def test_get_status_has_awaiting_fields_without_full_definitions(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_status(visitor=SimpleNamespace()))

    assert [f["key"] for f in result["field_reference"]] == spec.field_keys()
    assert result["next_field_key"] == "user_name"
    assert "awaiting_fields" not in result
    assert "field_definitions" not in result


@pytest.mark.asyncio
async def test_get_status_field_reference_excludes_server_internals(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_status(visitor=SimpleNamespace()))

    for f in result["field_reference"]:
        # Model-facing keys only — no server internals (validator, pre/post
        # processors, branches). ``hint`` (optional) is model-facing presentation
        # guidance and is allowed when set.
        assert {"key", "prompt", "required"} <= set(f.keys())
        assert set(f.keys()) <= {"key", "prompt", "guidance", "required", "hint"}
    assert "field_definitions" not in result
    assert "guidance_page" not in result
