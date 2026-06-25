"""Field-level ``hint`` — user-level guidance folded into the framed prompt."""

from __future__ import annotations

import pytest

from jvagent.action.interview.engine import run_pre_processors
from jvagent.action.interview.spec import (
    InterviewSpec,
    _parse_field,
    fields_reference,
)

_MARKER = "⁣"


def _spec(field):
    return InterviewSpec(name="t", fields=[field])


def test_hint_parsed_from_frontmatter():
    fd = _parse_field(
        {
            "key": "id_number",
            "prompt": "What's your ID number?",
            "guidance": "8-9 digits",
            "hint": "Mention they can upload a photo of the ID or type it.",
        },
        index=0,
    )
    assert fd.hint == "Mention they can upload a photo of the ID or type it."


def test_fields_reference_includes_hint_only_when_set():
    with_hint = _parse_field(
        {"key": "a", "prompt": "A?", "hint": "Keep it friendly."}, index=0
    )
    without = _parse_field({"key": "b", "prompt": "B?"}, index=1)
    assert fields_reference(_spec(with_hint))[0]["hint"] == "Keep it friendly."
    assert "hint" not in fields_reference(_spec(without))[0]


@pytest.mark.asyncio
async def test_hint_folded_into_framed_prompt_user_facing():
    fd = _parse_field(
        {
            "key": "id_number",
            "prompt": "What's your ID number?",
            "hint": "Mention they can upload a photo of the ID or type it.",
        },
        index=0,
    )
    directive, _ = await run_pre_processors(
        action=None, session=None, spec=_spec(fd), fdef=fd, visitor=None
    )
    # The orchestrator strips after-marker guidance before composing, so the hint
    # must reach the compose model in the USER-FACING (before-marker) portion,
    # framed as a compose instruction (the model applies it, doesn't echo it).
    user_part = directive.split(_MARKER, 1)[0]
    assert user_part.startswith("Tell the user or ask the user: What's your ID number?")
    assert "upload a photo of the ID" in user_part


@pytest.mark.asyncio
async def test_no_hint_leaves_prompt_unchanged():
    fd = _parse_field({"key": "b", "prompt": "What is your name?"}, index=0)
    directive, _ = await run_pre_processors(
        action=None, session=None, spec=_spec(fd), fdef=fd, visitor=None
    )
    assert "upload a photo" not in directive
    assert directive.startswith("Tell the user or ask the user: What is your name?")
