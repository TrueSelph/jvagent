"""Tests for interview__get_field handler."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession, save_session
from jvagent.action.interview_action.interview_action import InterviewAction

_ONBOARDING = Path(__file__).resolve().parent / "fixtures/skills/onboarding_interview"


@pytest.fixture
def onboarding_action():
    action = InterviewAction(
        metadata={"agent_dir": str(_ONBOARDING.parent.parent.parent)}
    )
    spec = load_interview_spec_from_skill(_ONBOARDING)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_get_field_returns_stored_value(onboarding_action):
    action, _spec = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.set_value("full_name", "Ada Lovelace")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv)
    await save_session(conv, session)

    result = json.loads(await action._handle_get_field("full_name", visitor=visitor))

    assert result["ok"] is True
    assert result["field"] == "full_name"
    assert result["value"] == "Ada Lovelace"
    assert result["exists"] is True


@pytest.mark.asyncio
async def test_get_field_unknown_field_returns_error(onboarding_action):
    action, _spec = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv)
    await save_session(conv, session)

    result = json.loads(await action._handle_get_field("not_a_field", visitor=visitor))

    assert result["ok"] is False
    assert result["error_code"] == "INVALID_FIELD"


@pytest.mark.asyncio
async def test_get_field_skipped_field_reports_not_exists(onboarding_action):
    action, _spec = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    session.skip_field("otp_code")
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv)
    await save_session(conv, session)

    result = json.loads(await action._handle_get_field("otp_code", visitor=visitor))

    assert result["ok"] is True
    assert result["field"] == "otp_code"
    assert result["exists"] is False
    assert result.get("value") is None
