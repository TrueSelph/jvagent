"""Unit tests for interview directive merge helpers."""

from __future__ import annotations

from jvagent.action.interview.directive_compose import (
    batch_failure_directive,
    batch_failure_status,
    compose_directives,
)
from jvagent.action.reply.reply_action import (
    DIRECTIVE_GUIDANCE_MARKER,
    user_facing_directive,
)

_RELAY = "Tell the user or ask the user:"


def _message(directive: str) -> str:
    raw = user_facing_directive(directive)
    if raw.lower().startswith(_RELAY.lower()):
        return raw[len(_RELAY) :].strip()
    return raw


def test_batch_failure_status_partial_success():
    failures = [{"error_code": "VALIDATION_FAILED", "field": "email"}]
    assert batch_failure_status(failures, stored_any=True) == "partial_success"


def test_batch_failure_status_validation_failed():
    failures = [
        {"error_code": "VALIDATION_FAILED", "field": "email"},
        {"error_code": "VALIDATION_FAILED", "field": "phone"},
    ]
    assert batch_failure_status(failures, stored_any=False) == "validation_failed"


def test_batch_failure_directive_humanizes_fields():
    directive = batch_failure_directive(
        [
            {"error_code": "VALIDATION_FAILED", "field": "user_name"},
            {"error_code": "VALIDATION_FAILED", "field": "email_address"},
        ]
    )
    assert "user name" in directive.lower()
    assert "email address" in directive.lower()
    assert "user_name" not in directive
    assert "email_address" not in directive


def test_batch_failure_directive_single_field_names_field_and_prompt():
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "unit",
                "error": (
                    "Ask: Please provide a more detailed response "
                    "(at least 2 characters)"
                ),
                "prompt": "What is your unit?",
            }
        ]
    )
    user = _message(directive).lower()
    assert user.startswith("what is your unit?")
    assert "at least 2 characters" in user
    assert "i still need a valid unit" not in user
    assert "ask:" not in user


def test_batch_failure_directive_partial_success_notes_saved_fields():
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "unit",
                "error": (
                    "Ask: Please provide a more detailed response "
                    "(at least 2 characters)"
                ),
                "prompt": "What is your unit?",
            }
        ],
        stored_any=True,
    )
    user = _message(directive)
    lower = user.lower()
    assert lower.startswith("i've saved the other details.")
    assert "\n\n" in user
    note, question = user.split("\n\n", 1)
    assert "saved the other details" in note.lower()
    assert question.lower().startswith("what is your unit?")
    assert "at least 2 characters" in question.lower()
    assert "i still need a valid unit" not in lower


def test_batch_failure_directive_strips_ask_prefix():
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "rank",
                "error": "Ask: Please provide a more detailed response (at least 2 characters)",
                "prompt": "What is your rank?",
            }
        ]
    )
    user = _message(directive)
    assert "Ask:" not in user
    assert not user.lower().startswith("tell the user")


def test_batch_failure_directive_skips_need_prefix_when_reason_names_field():
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "phone_number",
                "error": "Ask: Please provide a 10-digit phone number",
                "prompt": "What is your phone number?",
            }
        ]
    )
    user = _message(directive).lower()
    assert user.startswith("what is your phone number?")
    assert "10-digit phone number" in user
    assert "i still need a valid phone number" not in user


def test_batch_failure_directive_attaches_hint_as_model_only():
    hint = "The GDF unit you currently serve with."
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "unit",
                "error": "Ask: Please provide a more detailed response (at least 2 characters)",
                "prompt": "What is your unit?",
                "hint": hint,
            }
        ]
    )
    user, guidance = directive.split(DIRECTIVE_GUIDANCE_MARKER, 1)
    assert hint not in _message(user)
    assert hint in guidance


def test_batch_failure_directive_passthrough_call_directive():
    directive = batch_failure_directive(
        [
            {
                "error_code": "VALIDATION_FAILED",
                "field": "otp_code",
                "error": "OTP was not sent for this session.",
                "response_directive": 'Call interview__skip_field(field="otp_code").',
                "prompt": "Please enter the verification code sent to your email.",
            }
        ]
    )
    assert directive.startswith("Call interview__skip_field")
    assert "I still need" not in directive


def test_compose_directives_merges_user_parts_and_chains_calls():
    queue = [
        {
            "directive": "Tell the user: Thanks.",
            "stage": "post",
            "source": "hook",
            "field": None,
        },
        {
            "directive": "Call interview__next_field",
            "stage": "post",
            "source": "hook",
            "field": None,
        },
    ]
    merged = compose_directives(queue, fallback="fallback")
    assert merged.startswith("Tell the user or ask the user: Thanks.")
    assert "interview__next_field" in merged
