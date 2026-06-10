"""Regression tests for collectible vs prune path split and activation chaining."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.flow import (
    compute_active_path_for_prune,
    compute_collectible_path_names,
    compute_missing_required,
    resolve_next_field_name,
)
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import (
    load_interview_spec_from_skill,
    parse_interview_spec,
)
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)

_OPENING = "Hello my name is Eldon Marks. I'm here to sign up"


@pytest.fixture
def signup_spec():
    return load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


def _onboarding_gate_spec():
    """Minimal has_account branch (no else) — mirrors zoon onboarding gate."""
    skill_dir = Path(__file__).resolve().parent / "fixtures" / "skills"
    return parse_interview_spec(
        {
            "name": "onboarding_gate",
            "fields": [
                {
                    "key": "has_account",
                    "prompt": "Do you already have an account?",
                    "required": True,
                    "validator": "text",
                    "branches": [
                        {
                            "when": {"op": "equals", "value": "yes"},
                            "goto": "existing_email",
                        },
                        {
                            "when": {"op": "equals", "value": "no"},
                            "goto": "phone_number",
                        },
                    ],
                },
                {
                    "key": "existing_email",
                    "prompt": "What email is on your account?",
                    "required": True,
                    "validator": "text",
                },
                {
                    "key": "phone_number",
                    "prompt": "What is your phone number?",
                    "required": True,
                    "validator": "text",
                },
            ],
        },
        source_dir=str(skill_dir),
        default_name="onboarding_gate",
    )


@pytest.mark.asyncio
async def test_empty_signup_missing_only_first_field(signup_spec):
    session = InterviewSession(interview_type="signup_interview")
    load_fn = lambda _n: None

    collectible = await compute_collectible_path_names(session, signup_spec, load_fn)
    assert collectible == ["user_name"]

    missing = await compute_missing_required(session, signup_spec, load_fn)
    assert missing == ["user_name"]


@pytest.mark.asyncio
async def test_activation_opening_extracts_user_name(signup_action):
    action, _spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance=_OPENING)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    await action._handle_start("signup_interview", visitor, user_message=_OPENING)

    set_result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=visitor,
        )
    )
    assert set_result["ok"] is True
    assert set_result["fields"].get("user_name") == "Eldon Marks"
    assert set_result.get("next_tool") == "interview__next_field"


@pytest.mark.asyncio
async def test_onboarding_unanswered_has_account_does_not_project_existing_email():
    spec = _onboarding_gate_spec()
    session = InterviewSession(interview_type="onboarding_gate")
    load_fn = lambda _n: None

    collectible = await compute_collectible_path_names(session, spec, load_fn)
    assert collectible == ["has_account"]
    assert "existing_email" not in collectible

    active = await compute_active_path_for_prune(session, spec, load_fn)
    assert active == ["has_account"]
    assert "existing_email" not in active


@pytest.mark.asyncio
async def test_saturday_slot_store_chains_next_field(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Saturday 9:00 AM - 12:00 PM"},
        )
    )

    assert result["ok"] is True
    assert result.get("next_tool") == "interview__next_field"
    assert "Call interview__next_field" in (result.get("response_directive") or "")
    nxt = await resolve_next_field_name(session, spec, lambda _n: None)
    assert nxt == "training_format"


@pytest.mark.asyncio
async def test_idempotent_store_still_returns_next_tool(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Saturday 9:00 AM - 12:00 PM")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Saturday 9:00 AM - 12:00 PM"},
        )
    )

    assert result["ok"] is True
    assert result.get("next_tool") == "interview__next_field"


@pytest.mark.asyncio
async def test_premium_branch_active_path_includes_contact_via_else():
    skill_dir = Path(__file__).resolve().parent / "fixtures" / "skills"
    spec_data = {
        "name": "branch_demo",
        "fields": [
            {
                "key": "user_type",
                "prompt": "Premium or standard?",
                "required": True,
                "validator": "text",
                "branches": [
                    {"when": {"op": "equals", "value": "premium"}, "goto": "premium_q"},
                    {
                        "when": {"op": "equals", "value": "standard"},
                        "goto": "standard_q",
                    },
                ],
            },
            {
                "key": "premium_q",
                "prompt": "Premium?",
                "required": True,
                "else": "contact",
            },
            {
                "key": "standard_q",
                "prompt": "Standard?",
                "required": True,
                "else": "contact",
            },
            {"key": "contact", "prompt": "Contact?", "required": True},
        ],
    }
    spec = parse_interview_spec(
        spec_data,
        source_dir=str(skill_dir),
        default_name="branch_demo",
    )
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    session.set_value("contact", "555-0100")
    load_fn = lambda _n: None

    collectible = await compute_collectible_path_names(session, spec, load_fn)
    assert collectible == ["user_type", "premium_q"]

    active = await compute_active_path_for_prune(session, spec, load_fn)
    assert "contact" in active
    assert "premium_q" in active
