"""Tests for declarative activation seeding (seed_from_activation)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.activation_seed import (
    infer_field_from_activation,
    match_seed_from_activation,
    normalize_seed_from_activation,
    seed_field_from_activation,
)
from jvagent.action.interview.hooks import ACTIVATION_PHASE, call_hook, coerce_hook_result
from jvagent.action.interview.session import ACTIVATION_UTTERANCE_KEY, InterviewSession
from jvagent.action.interview.spec import FieldDef


def _intent_field(**validator_args) -> FieldDef:
    return FieldDef(
        key="interview_intent",
        prompt="intent?",
        validator="list",
        validator_args=validator_args,
    )


def test_match_longest_phrase_wins():
    seed = normalize_seed_from_activation(
        {
            "create_pre_alert": ["pre-alert", "pre alert"],
            "check_status": ["check status", "check the status"],
        }
    )
    assert (
        match_seed_from_activation(
            "I need a pre-alert for tracking 111",
            seed,
            allowed_values=["check_status", "create_pre_alert"],
        )
        == "create_pre_alert"
    )
    assert (
        match_seed_from_activation(
            "check the status of my package",
            seed,
            allowed_values=["check_status", "create_pre_alert"],
        )
        == "check_status"
    )


def test_match_exact_allowed_value():
    seed = normalize_seed_from_activation({"check_status": ["check status"]})
    assert (
        match_seed_from_activation("check_status", seed, allowed_values=["check_status"])
        == "check_status"
    )


@pytest.mark.asyncio
async def test_seed_field_from_activation_sets_value():
    field_def = _intent_field(
        allowed_items=["check_status", "create_pre_alert"],
        seed_from_activation={
            "create_pre_alert": ["pre-alert"],
            "check_status": ["check status"],
        },
    )
    session = InterviewSession(interview_type="test")
    session.context[ACTIVATION_UTTERANCE_KEY] = "create a pre-alert please"
    interview = MagicMock()
    interview._save_session = AsyncMock()

    result = coerce_hook_result(
        await call_hook(
            seed_field_from_activation,
            session=session,
            field_def=field_def,
            visitor=MagicMock(),
            interview_action=interview,
            phase=ACTIVATION_PHASE,
        )
    )

    assert session.get_value("interview_intent") == "create_pre_alert"
    assert result.get("suggested_value") == "create_pre_alert"


def test_infer_field_from_activation():
    session = InterviewSession(interview_type="test")
    session.context[ACTIVATION_UTTERANCE_KEY] = "pre-alert"
    field = _intent_field(
        allowed_items=["create_pre_alert"],
        seed_from_activation={"create_pre_alert": ["pre-alert"]},
    )
    assert infer_field_from_activation(session, field) == "create_pre_alert"
