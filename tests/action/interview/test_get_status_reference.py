"""get_status is the on-demand pull path for field_reference."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.mark.asyncio
async def test_get_status_returns_full_field_reference():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    session = InterviewSession(interview_type="signup_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(
        await action._handle_get_status(visitor=SimpleNamespace(utterance=""))
    )
    ref = result["field_reference"]
    assert [f["key"] for f in ref] == spec.field_keys()


@pytest.mark.asyncio
async def test_prepare_task_lock_turn_reinjects_field_reference():
    """The locked turn re-grounds the model with the field catalog so it picks
    correct keys instead of guessing (regression for failed extractions when the
    activation observation has aged out of history)."""
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    session = InterviewSession(interview_type="signup_interview")
    action._ensure_specs_loaded = AsyncMock()
    action._get_session = AsyncMock(return_value=session)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    prep = await action.prepare_task_lock_turn(
        "signup_interview", SimpleNamespace(utterance="")
    )
    assert prep.observations, "expected a server-prep status observation"
    obs = prep.observations[0]
    assert obs["kind"] == "server_prep"
    status = json.loads(obs["observation"])
    # Re-grounding carries the FULL field catalog (ADR-0026): a skill entered as a
    # pushed prerequisite / resumed via the drain is delivered terminally, so the
    # model may never run the activation turn — the re-ground must re-assert the
    # field_reference (keys + per-field guidance), not just the keys.
    assert status["field_keys"] == spec.field_keys()
    assert "field_reference" in status
    ref = status["field_reference"]
    assert isinstance(ref, list) and ref
    assert {f["key"] for f in ref} == set(spec.field_keys())
    assert any("guidance" in f for f in ref)  # per-field guidance present


@pytest.mark.asyncio
async def test_prepare_task_lock_turn_no_session_is_noop():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    action._ensure_specs_loaded = AsyncMock()
    action._get_session = AsyncMock(return_value=None)

    prep = await action.prepare_task_lock_turn(
        "signup_interview", SimpleNamespace(utterance="")
    )
    assert prep.observations == []
