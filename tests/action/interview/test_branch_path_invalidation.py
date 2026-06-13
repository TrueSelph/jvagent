"""Branch pivot must prune off-path fields only; preserve shared downstream answers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.flow import (
    resolve_next_field_name,
)
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
    parse_interview_spec,
)
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def branching_spec(tmp_path):
    skill_dir = tmp_path / "branch_demo"
    skill_dir.mkdir()
    spec_data = {
        "name": "branch_demo",
        "fields": [
            {
                "key": "user_type",
                "prompt": "Premium or standard?",
                "required": True,
                "validator": "text",
                "branches": [
                    {
                        "when": {"op": "equals", "value": "premium"},
                        "goto": "premium_q",
                    },
                    {
                        "when": {"op": "equals", "value": "standard"},
                        "goto": "standard_q",
                    },
                ],
                "else": "contact",
            },
            {
                "key": "premium_q",
                "prompt": "Premium features?",
                "required": True,
                "else": "contact",
            },
            {
                "key": "standard_q",
                "prompt": "Standard setup?",
                "required": True,
                "else": "contact",
            },
            {"key": "contact", "prompt": "Contact info?", "required": True},
        ],
    }
    return parse_interview_spec(
        spec_data, source_dir=str(skill_dir), default_name="branch_demo"
    )


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_pivot_preserves_shared_downstream(branching_spec):
    action = InterviewAction()
    action._registry._specs["branch_demo"] = branching_spec
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    session.set_value("premium_q", "widgets")
    session.set_value("contact", "555-0100")
    action._get_session_and_contract = AsyncMock(return_value=(session, branching_spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(fields={"user_type": "standard"})
    )

    assert result["ok"] is True
    assert session.get_value("user_type") == "standard"
    assert session.get_value("contact") == "555-0100"
    assert "premium_q" not in session.fields
    assert "premium_q" in result.get("pruned", [])


@pytest.mark.asyncio
async def test_signup_email_pivot_preserves_phone(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "jane@gmail.com")
    session.set_value("phone_number", "5551234567")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "jane@mail.com"},
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "jane@mail.com"
    assert session.get_value("phone_number") == "5551234567"
    nxt = await resolve_next_field_name(session, spec, lambda _n: None)
    assert nxt == "employer_name"


@pytest.mark.asyncio
async def test_signup_slot_pivot_preserves_downstream(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
    session.set_value("training_format", "Virtual")
    session.set_value("user_email", "jane@gmail.com")
    session.set_value("phone_number", "5551234567")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Monday 9:00 AM - 11:00 AM"},
        )
    )

    assert result["ok"] is True
    assert "training_format" not in session.fields
    assert session.get_value("user_email") == "jane@gmail.com"
    assert session.get_value("phone_number") == "5551234567"
    assert "training_format" in result.get("pruned", [])


@pytest.mark.asyncio
async def test_prune_clears_skipped_for_dead_branch(branching_spec):
    action = InterviewAction()
    action._registry._specs["branch_demo"] = branching_spec
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    session.set_value("premium_q", "orphan")
    session.skipped_fields.add("premium_q")
    action._get_session_and_contract = AsyncMock(return_value=(session, branching_spec))
    action._save_session = AsyncMock()

    await action._handle_set_fields(fields={"user_type": "standard"})

    assert "premium_q" not in session.fields
    assert "premium_q" not in session.skipped_fields


@pytest.mark.asyncio
async def test_post_pivot_extraction_stores_next_field(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
    session.set_value("training_format", "Virtual")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    pivot = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Monday 9:00 AM - 11:00 AM"},
        )
    )
    assert pivot["ok"] is True
    assert "training_format" not in session.fields
    assert "user_email" not in session.fields

    visitor = SimpleNamespace(utterance="my email is jane@gmail.com")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "jane@gmail.com"},
            visitor=visitor,
        )
    )

    assert result["ok"] is True
    assert session.get_value("user_email") == "jane@gmail.com"


@pytest.mark.asyncio
async def test_off_path_validation_failure_is_ignored_post_settlement(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "available_times": "Monday 9:00 AM - 11:00 AM",
                "training_format": "not-a-format",
            }
        )
    )

    assert result["ok"] is True
    assert "training_format" in result.get("ignored", [])
    by_field = {entry["field"]: entry for entry in result["results"]}
    assert by_field["training_format"]["ignored"] is True
    # Incremental settlement: the off-path field is skipped before its validator
    # runs, so it carries no validation error/validator and never stores.
    assert "error" not in by_field["training_format"]
    assert "validator" not in by_field["training_format"]
    assert by_field["training_format"]["stored"] is False
    assert session.get_value("available_times") == "Monday 9:00 AM - 11:00 AM"
    assert "training_format" not in session.fields
