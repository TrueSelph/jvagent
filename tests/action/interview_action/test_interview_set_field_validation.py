"""Tests for inline per-question validation in interview__set_field."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.interview_action.interview_action import (
    InterviewAction,
)
from jvagent.action.interview_action.interview_loader import (
    QuestionDef,
    load_interview_spec_from_skill,
    resolve_validator_def,
    resolve_validator_kwargs,
)
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.validators import get_validator

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"
_PRE_ALERT_SKILL = _SKILLS_DIR / "pre_alert_interview"
_ONBOARDING_SKILL = _SKILLS_DIR / "onboarding_interview"


def test_phone_number_pre_and_post_tools_parsed():
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    q = contract.get_question("phone_number")
    assert q is not None
    assert q.pre_tools == ["get_phone_number"]
    assert q.post_tools == ["verify_phone_number"]
    assert q.resolved_pre_tools() == ["get_phone_number"]


def test_email_post_tools_and_otp_code_parsed():
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    email_q = contract.get_question("email")
    assert email_q is not None
    assert email_q.post_tools == ["verify_email"]
    assert email_q.pre_tools == ["suggest_email_from_task"]

    otp_q = contract.get_question("otp_code")
    assert otp_q is not None
    assert otp_q.required is False
    assert otp_q.post_tools == []
    assert otp_q.validator["function"] == "validate_otp_code"

    send_otp_tool = next(t for t in contract.tools if t.name == "send_otp")
    assert send_otp_tool.function == "send_otp"


def test_tracking_number_post_tools_parsed():
    contract = load_interview_spec_from_skill(_PRE_ALERT_SKILL)
    q = contract.get_question("tracking_number")
    assert q is not None
    assert q.post_tools == ["check_tracking_status"]


def test_inline_validator_parsed_for_tracking_number():
    contract = load_interview_spec_from_skill(_PRE_ALERT_SKILL)
    q = contract.get_question("tracking_number")
    assert isinstance(q.validator, dict)
    assert q.validator["function"] == "validate_tracking_number"
    assert "name" not in q.validator


def test_resolve_validator_def_custom_tracking():
    contract = load_interview_spec_from_skill(_PRE_ALERT_SKILL)
    q = contract.get_question("tracking_number")
    vdef = resolve_validator_def(q, contract)
    assert vdef is not None
    assert vdef.name == "validate_tracking_number"
    assert get_validator(vdef.name) is None
    kwargs = resolve_validator_kwargs(q, vdef)
    assert kwargs.get("min_length") == 10


def test_resolve_validator_def_builtin_description():
    contract = load_interview_spec_from_skill(_PRE_ALERT_SKILL)
    q = contract.get_question("description")
    vdef = resolve_validator_def(q, contract)
    assert vdef is not None
    assert vdef.name == "description"
    assert get_validator(vdef.name) is not None
    kwargs = resolve_validator_kwargs(q, vdef)
    assert kwargs.get("min_length") == 10
    assert kwargs.get("max_length") == 500


def test_resolve_validator_def_builtin_email():
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    q = contract.get_question("email")
    vdef = resolve_validator_def(q, contract)
    assert vdef is not None
    assert vdef.name == "email"
    assert get_validator(vdef.name) is not None


def test_legacy_builtin_marker_resolves_to_function_name():
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    q = QuestionDef(
        name="phone_number",
        question="Phone?",
        validator={
            "name": "builtin",
            "function": "phone",
            "kwargs": {"exact_length": 10},
        },
    )
    vdef = resolve_validator_def(q, contract)
    assert vdef is not None
    assert vdef.name == "phone"
    assert get_validator(vdef.name) is not None


def test_id_number_has_no_alternate_validator():
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    q = contract.get_question("id_number")
    assert isinstance(q.validator, dict)
    assert q.validator["function"] == "validate_id_number"
    assert "alternate_validator" not in q.validator


@pytest.fixture
def pre_alert_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(_PRE_ALERT_SKILL)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.fixture
def onboarding_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.mark.asyncio
async def test_set_field_rejects_short_tracking_number(pre_alert_action):
    action, contract = pre_alert_action
    session = InterviewSession(interview_type="pre_alert_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))

    result = json.loads(
        await action._handle_set_field(field="tracking_number", value="123")
    )

    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert result["valid"] is False
    assert result["error_code"] == "VALIDATION_FAILED"
    assert "tracking_number" not in session.fields


@pytest.mark.asyncio
async def test_set_field_stores_cleaned_tracking_number(pre_alert_action):
    action, contract = pre_alert_action
    session = InterviewSession(interview_type="pre_alert_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    from unittest.mock import patch

    async def _passthrough_post_tools(*args, **kwargs):
        return args[-1]

    with patch(
        "jvagent.action.interview_action.runtime.pipeline.run_post_tools",
        side_effect=_passthrough_post_tools,
    ):
        result = json.loads(
            await action._handle_set_field(
                field="tracking_number", value="abc291421515335xyz"
            )
        )

    assert result["ok"] is True
    assert result["status"] == "active"
    assert session.get_value("tracking_number") == "291421515335"
    assert result["value"] == "291421515335"
    assert "next_questions" not in result


@pytest.mark.asyncio
async def test_init_seeds_tracking_from_user_message(pre_alert_action):
    action, contract = pre_alert_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)
    from unittest.mock import patch

    async def _passthrough_post_tools(*args, **kwargs):
        return args[-1]

    with patch(
        "jvagent.action.interview_action.runtime.pipeline.run_post_tools",
        side_effect=_passthrough_post_tools,
    ):
        result = json.loads(
            await action._handle_start(
                "pre_alert_interview",
                user_message="Please track my package 291421515335",
            )
        )

    assert result["status"] == "active"
    assert result["fields"].get("tracking_number") == "291421515335"
    assert "tracking_number" in result.get("seeded_fields", [])
    assert "tracking_number" not in [
        q["name"] for q in result.get("next_questions", [])
    ]


@pytest.mark.asyncio
async def test_init_without_extractable_data_asks_first_question(pre_alert_action):
    action, contract = pre_alert_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    result = json.loads(
        await action._handle_start(
            "pre_alert_interview",
            user_message="I want to track my package",
        )
    )

    assert result["ok"] is True
    assert result["status"] == "active"
    assert "tracking_number" not in result.get("fields", {})
    assert "response_directive" not in result


@pytest.mark.asyncio
async def test_next_question_runs_pre_tools_on_whatsapp(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    visitor = SimpleNamespace(channel="whatsapp", user_id="5912345678")

    result = json.loads(await action._handle_next_question(visitor=visitor))

    assert result["ok"] is True
    assert result["pre_tools_results"][0]["tool"] == "get_phone_number"
    assert result["pre_tools_results"][0]["value"] == "5912345678"
    assert "5912345678" in result["response_directive"]
    assert result["next_questions"][0]["suggested_value"] == "5912345678"


@pytest.mark.asyncio
async def test_next_question_falls_back_to_phone_question_off_whatsapp(
    onboarding_action,
):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    visitor = SimpleNamespace(channel="web", user_id="5912345678")

    result = json.loads(await action._handle_next_question(visitor=visitor))

    assert result["ok"] is True
    assert "What is your best phone number?" in result["response_directive"]
    assert "suggested_value" not in result["next_questions"][0]


@pytest.mark.asyncio
async def test_init_seeds_phone_from_user_message(onboarding_action):
    action, contract = onboarding_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    result = json.loads(
        await action._handle_start(
            "onboarding_interview",
            user_message="My number is 5912345678",
        )
    )

    assert result["fields"].get("phone_number") == "5912345678"
    assert "phone_number" in result.get("seeded_fields", [])


@pytest.mark.asyncio
async def test_set_field_id_number_accepts_passport(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="id_number", value="AB1234567")
    )

    assert result["status"] == "active"
    assert session.get_value("id_number") == "AB1234567"


@pytest.mark.asyncio
async def test_post_tools_verify_phone_number_after_set_field_not_registered(
    onboarding_action,
):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    api = MagicMock()
    api.find_customer_by_phone = AsyncMock(return_value={"customer": None})

    visitor = MagicMock()
    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="phone_number",
                value="5926431530",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    assert "post_tools_results" in result
    assert result["post_tools_results"][0]["tool"] == "verify_phone_number"
    assert result["post_tools_results"][0]["ok"] is True
    assert result["post_tools_results"][0]["system_message"] == (
        "No existing customer found with this phone number. Proceed with onboarding."
    )
    assert "phone" not in result["post_tools_results"][0]
    assert "customer" not in result["post_tools_results"][0]
    assert result["exists"] is False
    assert result["status"] == "not_registered"
    assert "next_questions" not in result
    assert result["response_directive"] == "Call interview__next_question."
    assert result["next_tool"] == "interview__next_question"
    api.find_customer_by_phone.assert_awaited_once_with("5926431530")


@pytest.mark.asyncio
async def test_post_tools_verify_phone_number_stops_when_registered(
    onboarding_action,
):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    action._close_task = AsyncMock()
    action._clear_interview_session = AsyncMock()

    api = MagicMock()
    api.find_customer_by_phone = AsyncMock(
        return_value={"customer": {"id": "cust-1", "name": "Jane"}}
    )

    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.conversation.context = {}
    visitor.conversation.save = AsyncMock()

    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="phone_number",
                value="5926431530",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    assert "post_tools_results" in result
    assert result["post_tools_results"][0]["system_message"] == (
        "This phone number is already registered with Zoon."
    )
    assert "phone" not in result["post_tools_results"][0]
    assert "customer" not in result["post_tools_results"][0]
    assert result["exists"] is True
    assert result["status"] == "customer_exists"
    assert "what is your email" not in result["response_directive"].lower()
    assert "account" in result["response_directive"].lower()


@pytest.mark.asyncio
async def test_post_tools_verify_email_no_customer(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("phone_number", "5926431530")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    api = MagicMock()
    api.find_customer_by_email = AsyncMock(return_value={"customer": None})

    visitor = MagicMock()
    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="email",
                value="newuser@example.com",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    assert result["post_tools_results"][0]["tool"] == "verify_email"
    assert result["post_tools_results"][0]["ok"] is True
    assert "otp_pending" not in result["post_tools_results"][0]
    api.find_customer_by_email.assert_awaited_once_with("newuser@example.com")


@pytest.mark.asyncio
async def test_post_tools_verify_email_same_phone_continues(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("phone_number", "5937437843")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    api = MagicMock()
    api.find_customer_by_email = AsyncMock(
        return_value={
            "customer": {
                "id": 47338,
                "phone": ["5937437843"],
                "account_number": "GEO100188",
            }
        }
    )
    api.request_whatsapp_otp = AsyncMock()

    visitor = MagicMock()
    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="email",
                value="sdemo@dem.com",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    assert result["post_tools_results"][0]["tool"] == "verify_email"
    assert "otp_pending" not in result["post_tools_results"][0]
    api.request_whatsapp_otp.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_tools_verify_email_different_phone_sets_otp_pending(
    onboarding_action,
):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("phone_number", "5926431530")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    api = MagicMock()
    api.find_customer_by_email = AsyncMock(
        return_value={
            "customer": {
                "id": 47338,
                "phone": ["5937437843"],
                "account_number": "GEO100188",
            }
        }
    )
    api.request_whatsapp_otp = AsyncMock()

    visitor = MagicMock()
    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="email",
                value="sdemo@dem.com",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    post = result["post_tools_results"][0]
    assert post["tool"] == "verify_email"
    assert post["otp_pending"] is True
    assert "send_otp" in post["response_directive"].lower()
    api.request_whatsapp_otp.assert_not_awaited()
    assert session.context.get("otp_pending") is True


@pytest.mark.asyncio
async def test_validate_otp_code_success_completes_interview(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("phone_number", "5926431530")
    session.set_value("email", "sdemo@dem.com")
    session.context = {
        "otp_sent": True,
        "otp_pending": True,
        "otp_target_phone": "5926431530",
        "email_lookup_customer": {
            "account_number": "GEO100188",
            "name": "Rick Smithh",
            "primary_mail": "sdemo@dem.com",
            "id_number": "32463893",
            "phone": ["5937437843"],
        },
    }
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    action._close_task = AsyncMock()
    action._clear_interview_session = AsyncMock()

    api = MagicMock()
    api.confirm_whatsapp_otp = AsyncMock(
        return_value={"message": "Your WhatsApp number was updated successfully."}
    )

    skill_handle = MagicMock()
    skill_handle.update = AsyncMock()
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.conversation.context = {}
    visitor.conversation.save = AsyncMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=[skill_handle])

    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="otp_code",
                value="123456",
                visitor=visitor,
            )
        )

    assert result["ok"] is True
    assert result["interview_complete"] is True
    assert "GEO100188" in result["response_directive"]
    api.confirm_whatsapp_otp.assert_awaited_once_with(
        "sdemo@dem.com", "123456", "5926431530"
    )
    action._clear_interview_session.assert_awaited_once()
    skill_handle.update.assert_awaited_once_with(
        fields={
            "phone_number": "5926431530",
            "email": "sdemo@dem.com",
            "full_name": "Rick Smithh",
            "id_number": "32463893",
        },
        account_number="GEO100188",
        flow_mode="onboard",
    )
    assert visitor.conversation.context["user_is_onboarded"] == "completed"
    assert visitor.conversation.context["customer_id"] == "GEO100188"


@pytest.mark.asyncio
async def test_validate_otp_code_invalid_without_complete(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("phone_number", "5926431530")
    session.set_value("email", "sdemo@dem.com")
    session.context = {
        "otp_sent": True,
        "otp_pending": True,
        "otp_target_phone": "5926431530",
        "email_lookup_customer": {"account_number": "GEO100188"},
    }
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    action._clear_interview_session = AsyncMock()

    api = MagicMock()
    api.confirm_whatsapp_otp = AsyncMock(
        return_value={"status": 400, "message": "Invalid or expired verification code."}
    )

    visitor = MagicMock()
    with patch(
        "jvagent.action.base.Action.get_action",
        new=AsyncMock(return_value=api),
    ):
        result = json.loads(
            await action._handle_set_field(
                field="otp_code",
                value="000000",
                visitor=visitor,
            )
        )

    assert result["ok"] is False
    assert "invalid" in result["error"].lower()
    assert "resend" in result["response_directive"].lower()
    action._clear_interview_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_otp_code_rejects_when_otp_not_sent(onboarding_action):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.context = {"otp_sent": False}
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    visitor = MagicMock()
    result = json.loads(
        await action._handle_set_field(
            field="otp_code",
            value="123456",
            visitor=visitor,
        )
    )

    assert result["ok"] is False
    assert "skip_field" in result["response_directive"].lower()
