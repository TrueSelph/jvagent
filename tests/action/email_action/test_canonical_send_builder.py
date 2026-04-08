"""Tests for canonical send body parsing (shared builder)."""

import pytest
from jvspatial.api.exceptions import ValidationError

from jvagent.action.email_action.canonical_send_builder import build_canonical_send_message


async def _fixed_sender() -> tuple[str, str | None]:
    return "sender@example.com", "Sender Name"


def _effective_name() -> str | None:
    return "Env Name"


@pytest.mark.asyncio
async def test_build_canonical_minimal_text():
    msg = await build_canonical_send_message(
        {
            "to": "a@b.com",
            "text_content": "hello",
        },
        action_id="act1",
        resolve_sender=_fixed_sender,
        effective_sender_name=_effective_name,
    )
    assert msg.to_email == "a@b.com"
    assert msg.text_content == "hello"
    assert msg.subject == "Message"
    assert msg.sender_email == "sender@example.com"


@pytest.mark.asyncio
async def test_build_canonical_rejects_bad_to():
    with pytest.raises(ValidationError, match="to"):
        await build_canonical_send_message(
            {"to": "not-an-email", "text_content": "x"},
            action_id="act1",
            resolve_sender=_fixed_sender,
            effective_sender_name=_effective_name,
        )


@pytest.mark.asyncio
async def test_build_canonical_requires_body():
    with pytest.raises(ValidationError, match="htmlContent or textContent"):
        await build_canonical_send_message(
            {"to": "a@b.com"},
            action_id="act1",
            resolve_sender=_fixed_sender,
            effective_sender_name=_effective_name,
        )


@pytest.mark.asyncio
async def test_build_canonical_accepts_camel_case():
    msg = await build_canonical_send_message(
        {
            "to": "a@b.com",
            "textContent": "hi",
            "htmlContent": "<p>Hi</p>",
            "subject": "S",
        },
        action_id="act1",
        resolve_sender=_fixed_sender,
        effective_sender_name=_effective_name,
    )
    assert msg.html_content == "<p>Hi</p>"
    assert msg.text_content == "hi"
    assert msg.subject == "S"


@pytest.mark.asyncio
async def test_build_canonical_uses_sender_from_body():
    msg = await build_canonical_send_message(
        {
            "to": "a@b.com",
            "text_content": "x",
            "sender_email": "from@example.com",
            "sender_name": "From",
        },
        action_id="act1",
        resolve_sender=_fixed_sender,
        effective_sender_name=_effective_name,
    )
    assert msg.sender_email == "from@example.com"
    assert msg.sender_name == "From"
