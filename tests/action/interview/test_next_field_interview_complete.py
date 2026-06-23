"""A pre_processor that returns interview_complete must terminate the interview.

Regression: handle_next_field surfaced the early-out's directive ("already done")
but ignored interview_complete, leaving the interview active with an empty
session AND the skill task open. A later utterance then re-entered that orphaned
task and re-asked the first field.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview import engine, tasks
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"
_ONBOARDING_SKILL = _SKILLS_DIR / "onboarding_interview"


@pytest.fixture
def onboarding_action():
    action = InterviewAction()
    contract = load_interview_spec_from_skill(_ONBOARDING_SKILL)
    action._registry._specs[contract.name] = contract
    return action, contract


@pytest.mark.asyncio
async def test_next_field_pre_processor_interview_complete_closes_task(
    onboarding_action, monkeypatch
):
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    cleared = {}

    async def _clear(visitor=None, *, retain_context_keys=None):
        cleared["retain"] = retain_context_keys

    action._clear_interview_session = AsyncMock(side_effect=_clear)

    async def _fake_pre(*_a, **_k):
        return (
            "You're already verified for this conversation.",
            {
                "interview_complete": True,
                "retain_context_keys": ["zoon_account"],
                "pre_tools_results": [],
            },
        )

    monkeypatch.setattr(engine, "run_pre_processors", _fake_pre)

    closed = {}

    async def _fake_close(visitor=None, status="completed", spec_name=None):
        closed["status"] = status
        closed["spec_name"] = spec_name

    monkeypatch.setattr(tasks, "close_task", _fake_close)

    result = json.loads(await action._handle_next_field(visitor=MagicMock()))

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["interview_complete"] is True
    assert "already verified" in result["response_directive"]
    # Session cleared (retaining the durable keys) AND the task closed — no orphan.
    action._clear_interview_session.assert_awaited()
    assert cleared["retain"] == ["zoon_account"]
    assert closed["status"] == "completed"
    assert closed["spec_name"] == "onboarding_interview"


@pytest.mark.asyncio
async def test_next_field_pre_processor_no_complete_presents_field(
    onboarding_action, monkeypatch
):
    # Control: without interview_complete the field is presented as usual and the
    # task is NOT closed.
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()

    closed = {"called": False}

    async def _fake_close(*_a, **_k):
        closed["called"] = True

    monkeypatch.setattr(tasks, "close_task", _fake_close)

    result = json.loads(await action._handle_next_field(visitor=MagicMock()))

    assert result["ok"] is True
    assert "next_field" in result
    assert result.get("interview_complete") is not True
    assert closed["called"] is False


@pytest.mark.asyncio
async def test_set_fields_hook_completion_closes_task(onboarding_action, monkeypatch):
    # Store-phase symmetry: a post_processor returning interview_complete must
    # also close the task, not just clear the session.
    action, contract = onboarding_action
    session = InterviewSession(interview_type="onboarding_interview")
    action._get_session_and_contract = AsyncMock(return_value=(session, contract))
    action._save_session = AsyncMock()
    action._clear_interview_session = AsyncMock()

    async def _fake_post(*_a, **_k):
        return (
            [],
            {"interview_complete": True, "retain_context_keys": ["zoon_account"]},
        )

    monkeypatch.setattr(engine, "run_post_processors", _fake_post)

    closed = {}

    async def _fake_close(visitor=None, status="completed", spec_name=None):
        closed["status"] = status
        closed["spec_name"] = spec_name

    monkeypatch.setattr(tasks, "close_task", _fake_close)

    result = json.loads(
        await action._handle_set_fields(
            fields={"phone_number": "5912345678"}, visitor=MagicMock()
        )
    )

    assert result["interview_complete"] is True
    action._clear_interview_session.assert_awaited()
    assert closed.get("status") == "completed"
    assert closed.get("spec_name") == "onboarding_interview"
