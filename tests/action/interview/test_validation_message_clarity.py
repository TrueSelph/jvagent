"""Validation failure envelopes: per-field validator errors in batch mode."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    FieldDef,
    InterviewSpec,
    load_interview_spec_from_skill,
)
from jvagent.action.reply.reply_action import (
    DIRECTIVE_GUIDANCE_MARKER,
    user_facing_directive,
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
    # User-facing re-ask names the humanized fields and surfaces the validator
    # message — without raw keys or engine internals.
    directive = result["response_directive"].lower()
    assert "valid values" in directive
    assert "user name" in directive and "available times" in directive
    assert "user_name" not in directive  # raw snake_case key must not leak


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


@pytest.mark.asyncio
async def test_signup_phone_local_seven_digits_accepted_with_country_code(
    signup_action,
):
    """Bare local numbers must go through set_fields — not a reply-only country ask.

    Regression: model followed a hint to ask for country/area code conversationally
    instead of calling set_fields; validator never ran and the user had to nudge.
    """
    action, contract = signup_action
    phone = contract.get_field("phone_number")
    assert phone is not None
    assert phone.validator_args.get("country_code") == "592"
    assert phone.validator_args.get("exact_length") == 10
    assert "ask for country" not in (phone.hint or "").lower()

    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    session.set_value("employer_name", "V75 Inc.")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="6415808")

    result = json.loads(
        await action._handle_set_fields(
            fields={"phone_number": "6415808"}, visitor=visitor
        )
    )

    assert result["ok"] is True
    assert session.get_value("phone_number") == "5926415808"


@pytest.mark.asyncio
async def test_signup_phone_too_short_returns_length_reask(signup_action):
    action, contract = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="123")

    result = json.loads(
        await action._handle_set_fields(fields={"phone_number": "123"}, visitor=visitor)
    )

    assert result["ok"] is False
    assert "10-digit" in (result.get("response_directive") or "").lower()
    assert "phone_number" not in session.fields
    assert "phone_number" not in (result.get("response_directive") or "")


@pytest.mark.asyncio
async def test_partial_success_names_failed_text_field():
    spec = InterviewSpec(
        name="mini_onboarding",
        fields=[
            FieldDef(
                key="name",
                prompt="What is your full legal name?",
                validator="name",
            ),
            FieldDef(
                key="unit",
                prompt="What is your unit?",
                hint="The GDF unit you currently serve with.",
                validator="text",
                validator_args={"min_length": 2},
            ),
        ],
    )
    action = InterviewAction()
    action._registry._specs[spec.name] = spec
    session = InterviewSession(interview_type="mini_onboarding")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(utterance="Michael Anderson, unit 8")

    result = json.loads(
        await action._handle_set_fields(
            fields={"name": "Michael Anderson", "unit": "8"},
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "partial_success"
    assert session.get_value("name") == "Michael Anderson"
    assert "unit" not in session.fields

    directive = result["response_directive"]
    raw = user_facing_directive(directive)
    prefix = "Tell the user or ask the user:"
    user = (
        raw[len(prefix) :].strip().lower()
        if raw.lower().startswith(prefix.lower())
        else raw.lower()
    )
    assert "saved the other details" in user
    assert "what is your unit?" in user
    assert "at least 2 characters" in user
    assert "i still need a valid unit" not in user
    assert "ask:" not in user
    assert "\n\n" in raw

    _, guidance = directive.split(DIRECTIVE_GUIDANCE_MARKER, 1)
    assert "gdf unit" not in user
    assert "gdf unit" in guidance.lower()
