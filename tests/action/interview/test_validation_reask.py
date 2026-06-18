"""validation_guidance_directive must not duplicate the ask."""

from __future__ import annotations

from jvagent.action.interview.hooks import validation_guidance_directive
from jvagent.action.reply.reply_action import user_facing_directive


def _user_text(directive: str) -> str:
    # strip the "Tell the user:" prefix, then drop model-only guidance after the marker
    body = (
        directive[len("Tell the user:") :]
        if directive.lower().startswith("tell the user:")
        else directive
    )
    return user_facing_directive(body)


def test_complete_sentence_error_not_duplicated_with_question():
    d = validation_guidance_directive(
        "Please provide a phone number with at least 7 digits.",
        question_text="What is your WhatsApp mobile number? (include country code on web)",
    )
    user = _user_text(d)
    assert user == "Please provide a phone number with at least 7 digits."
    assert "What is your WhatsApp" not in user  # question not appended


def test_terse_fragment_gets_question_appended():
    d = validation_guidance_directive(
        "Invalid value", question_text="What is the price?"
    )
    user = _user_text(d)
    assert user == "Invalid value What is the price?"


def test_question_ending_error_is_self_contained():
    d = validation_guidance_directive(
        "What currency?", question_text="What currency is the price in?"
    )
    user = _user_text(d)
    assert user == "What currency?"


def test_prefixed_error_strips_prefix_and_skips_question():
    d = validation_guidance_directive(
        "Tell the user: Pick a valid slot", question_text="What times?"
    )
    user = _user_text(d)
    assert user == "Pick a valid slot"
