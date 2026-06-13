"""Batch set_fields / get_fields and correction paths."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import (
    InterviewSession,
    InterviewStatus,
)
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
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
async def test_set_fields_partial_success_keeps_valid_values(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "user_name": "Jane Doe",
                "available_times": "Tuesdays at 9",
            },
            visitor=SimpleNamespace(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "partial_success"
    assert session.get_value("user_name") == "Jane Doe"
    assert "available_times" not in session.fields
    stored = [e["field"] for e in result["results"] if e["stored"]]
    assert stored == ["user_name"]
    failed = [e for e in result["results"] if not e.get("stored")]
    assert failed[0]["field"] == "available_times"


@pytest.mark.asyncio
async def test_set_fields_multi_field_hooks_use_single_compact_directive(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Jane Doe")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "available_times": "Monday 9:00 AM - 11:00 AM",
                "user_email": "jane@mail.com",
            },
            visitor=SimpleNamespace(),
        )
    )

    assert result["ok"] is True
    assert "response_directives_queue" not in result
    assert "results" in result
    assert result.get("response_directive")


@pytest.mark.asyncio
async def test_set_fields_rejects_under_extracted_compound_payload(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(
        utterance=(
            "I want to sign up and my name is Jane Doe, my email is jane@mail.com, "
            "and I am available Monday at 9am"
        )
    )

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"},
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "UNDER_EXTRACTED"
    assert "suggested_additional_keys" in result
    assert "user_email" in result["suggested_additional_keys"]
    assert "available_times" in result["suggested_additional_keys"]


@pytest.mark.asyncio
async def test_set_fields_unknown_alias_keys_then_canonical_retry(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    alias_visitor = SimpleNamespace(
        utterance=(
            "Eldon Marks. Availability Monday 9:00 AM - 11:00 AM. "
            "Company ACME. Email eldon@mail.com"
        )
    )
    first = json.loads(
        await action._handle_set_fields(
            fields={
                "user_name": "Eldon Marks",
                "availability": "Monday 9:00 AM - 11:00 AM",
                "company": "ACME",
                "email": "eldon@mail.com",
            },
            visitor=alias_visitor,
        )
    )

    assert first["status"] == "partial_success"
    first_failed = [e for e in first["results"] if not e.get("stored")]
    assert first_failed and first_failed[0].get("error")
    assert session.get_value("user_name") == "Eldon Marks"
    assert "available_times" in str(first.get("system_message", ""))

    corrected_visitor = SimpleNamespace(
        utterance="I can do Monday 9:00 AM - 11:00 AM and email me at eldon@mail.com"
    )
    second = json.loads(
        await action._handle_set_fields(
            fields={
                "available_times": "Monday 9:00 AM - 11:00 AM",
                "user_email": "eldon@mail.com",
            },
            visitor=corrected_visitor,
        )
    )

    assert second["ok"] is True
    assert session.get_value("available_times") == "Monday 9:00 AM - 11:00 AM"
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
    from jvagent.action.interview.tools import build_tools

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


@pytest.mark.asyncio
async def test_set_fields_return_is_slim(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    visitor = SimpleNamespace(utterance="Jane Doe")
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"}, visitor=visitor
        )
    )

    assert result["ok"] is True
    entry = result["results"][0]
    # Brief result: which field, saved or not, the original value submitted.
    assert entry["field"] == "user_name"
    assert entry["stored"] is True
    assert entry["value"] == "Jane Doe"
    assert "validator" not in entry  # server internals never surface
    for gone in (
        "field_updates",
        "stored_fields",
        "fields_delta",
        "failed_fields",
        "awaiting_fields",
        "field_keys",
        "guidance_page",
    ):
        assert gone not in result
    assert "response_directive" in result


@pytest.mark.asyncio
async def test_set_fields_completion_return_is_slim(signup_action, monkeypatch):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    action._clear_interview_session = AsyncMock()

    from jvagent.action.interview import engine as eng

    async def _complete_validator(*a, **k):
        return {"valid": True, "value": "x", "interview_complete": True}

    monkeypatch.setattr(eng, "run_validator", _complete_validator)

    result = json.loads(
        await action._handle_set_fields(
            fields={spec.field_keys()[0]: "x"},
            visitor=SimpleNamespace(utterance="x"),
        )
    )

    assert result["status"] == "completed"
    assert result["interview_complete"] is True
    assert "results" in result
    assert "fields" not in result
    assert "field_updates" not in result


@pytest.mark.asyncio
async def test_batch_note_pairs_with_final_next_not_stale(signup_action):
    """Processor note + the next question computed from FINAL settled state.

    Storing user_email (opens @mail.com branch) AND employer_name in one call must
    not leave the directive asking employer_name (already stored) — it asks the real
    next field (phone), with the work-email note preserved.
    """
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com", "employer_name": "Version Inc."},
            visitor=SimpleNamespace(utterance="x"),
        )
    )

    assert result["ok"] is True
    assert session.get_value("employer_name") == "Version Inc."
    directive = result["response_directive"].lower()
    assert "work email" in directive  # note preserved
    assert "phone" in directive  # real next field
    assert "company or organization" not in directive  # stored field not re-asked


@pytest.mark.asyncio
async def test_skipped_fields_surface_in_set_fields_after_skip(signup_action):
    """A correction after skipping an optional field reports skipped_fields so the
    model stays aware and does not re-prompt the skipped field."""
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    session.set_value("user_name", "Eldon Marks")
    session.set_value("available_times", "Monday 9:00 AM - 11:00 AM")
    session.set_value("user_email", "eldon@mail.com")
    session.set_value("employer_name", "Version Inc.")
    session.skip_field("phone_number")
    session.status = InterviewStatus.REVIEW
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks Jr"},
            visitor=SimpleNamespace(utterance="fix name"),
        )
    )

    assert result["ok"] is True
    assert result["skipped_fields"] == ["phone_number"]
    # does not re-route to collecting the skipped field
    assert result.get("next_tool") == "interview__review"


@pytest.mark.asyncio
async def test_next_field_optional_signals_skip(signup_action):
    """An optional next field must carry required:False and a skip directive so a
    user decline routes to interview__skip_field."""
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    for k, v in [
        ("user_name", "Eldon Marks"),
        ("available_times", "Monday 9:00 AM - 11:00 AM"),
        ("user_email", "eldon@mail.com"),
        ("employer_name", "Version Inc."),
    ]:
        session.set_value(k, v)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_next_field(visitor=SimpleNamespace(utterance=""))
    )

    assert result["next_field"]["key"] == "phone_number"
    assert result["next_field"]["required"] is False
    assert "interview__skip_field" in result["response_directive"]
    assert "phone_number" in result["response_directive"]


@pytest.mark.asyncio
async def test_skip_field_without_key_skips_pending_field(signup_action):
    """A bare interview__skip_field() (no field_key) skips the current pending
    field — the model can decline the question just asked without naming it."""
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    for k, v in [
        ("user_name", "Eldon Marks"),
        ("available_times", "Monday 9:00 AM - 11:00 AM"),
        ("user_email", "eldon@mail.com"),
        ("employer_name", "Version Inc."),
    ]:
        session.set_value(k, v)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_skip_field("", visitor=SimpleNamespace(utterance="no"))
    )

    assert result["ok"] is True
    assert result["field"] == "phone_number"
    assert result["skipped_fields"] == ["phone_number"]
    assert "phone_number" in session.skipped_fields


@pytest.mark.asyncio
async def test_inline_next_question_carries_key_and_skip_path(signup_action):
    """When set_fields inlines an optional next question (bypassing next_field), it
    must still surface the canonical next_field_key and the skip path — otherwise the
    model asks the question with no key to later pass to skip_field."""
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={
                "user_name": "Eldon Marks",
                "available_times": "Monday at 9",
                "user_email": "eldon@mail.com",
                "employer_name": "Version Inc.",
            },
            visitor=SimpleNamespace(utterance="..."),
        )
    )

    assert result["next_field_key"] == "phone_number"
    directive = result["response_directive"]
    assert "phone" in directive.lower()  # inlined question
    assert "interview__skip_field" in directive  # skip path present
    assert '"field_key": "phone_number"' in directive  # with canonical key
