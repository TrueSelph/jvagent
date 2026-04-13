"""Tests for composed email-channel utterances."""

from unittest.mock import patch

import pytest

from jvagent.action.email_action.email_utterance import (
    build_email_interaction_utterance,
    compose_email_channel_utterance,
    extract_email_body_plain,
)


def test_extract_email_body_plain_prefers_plain():
    assert extract_email_body_plain({"BodyPlain": "p", "BodyHtml": "<b>h</b>"}) == "p"


def test_extract_email_body_plain_html_fallback():
    assert "h" in extract_email_body_plain({"BodyHtml": "<p>h</p>"})


def test_compose_email_channel_utterance_subject_only():
    assert compose_email_channel_utterance("S", "") == "S"
    assert compose_email_channel_utterance("", "") == "(no subject)"


def test_compose_email_channel_utterance_with_body():
    out = compose_email_channel_utterance("Hi", "Body line")
    assert out.startswith("Hi\n\n---\nEmail body:\nBody line")


@pytest.mark.asyncio
async def test_build_email_interaction_utterance_composes():
    class _Ag:
        max_statement_length = None

    data = {
        "email_inbound": {
            "Subject": "Subj",
            "BodyPlain": "Hello",
        }
    }
    with patch(
        "jvagent.action.email_action.email_utterance._interact_max_utterance_length",
        return_value=None,
    ):
        utt = await build_email_interaction_utterance(data, agent=_Ag())
    assert "Subj" in utt
    assert "---\nEmail body:\n" in utt
    assert "Hello" in utt


@pytest.mark.asyncio
async def test_build_email_interaction_utterance_truncates_full():
    class _Ag:
        max_statement_length = 100_000

    data = {
        "email_inbound": {
            "Subject": "S",
            "BodyPlain": "x" * 50,
        }
    }
    with patch(
        "jvagent.action.email_action.email_utterance._interact_max_utterance_length",
        return_value=30,
    ):
        utt = await build_email_interaction_utterance(data, agent=_Ag())
    assert len(utt) <= 33
    assert utt.endswith("...")


@pytest.mark.asyncio
async def test_build_email_interaction_utterance_final_max_chars_overrides_interact():
    class _Ag:
        max_statement_length = 100_000

    data = {
        "email_inbound": {
            "Subject": "Sub",
            "BodyPlain": "x" * 200,
        }
    }
    utt = await build_email_interaction_utterance(data, agent=_Ag(), final_max_chars=80)
    assert len(utt) == 83
    assert utt.endswith("...")


@pytest.mark.asyncio
async def test_build_email_body_cap_uses_agent_max_when_large():
    class _Ag:
        max_statement_length = 20_000

    long_body = "y" * 25_000
    data = {"email_inbound": {"Subject": "Q", "BodyPlain": long_body}}
    with patch(
        "jvagent.action.email_action.email_utterance._interact_max_utterance_length",
        return_value=500_000,
    ):
        utt = await build_email_interaction_utterance(data, agent=_Ag())
    assert utt.endswith("...")
    body_part = utt.split("Email body:\n", 1)[-1]
    assert len(body_part) == 20_003
