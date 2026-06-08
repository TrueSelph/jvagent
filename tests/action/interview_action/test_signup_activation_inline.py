"""Activation with inline answers — evaluation obs → set_field → merged next question."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession, save_session
from jvagent.action.interview_action.interview_action import InterviewAction
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)

_OPENING = "Hello my name is Eldon Marks. I'm here to sign up"


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


@pytest.mark.asyncio
async def test_on_skill_activate_notes_message_evaluation(signup_action):
    action, _spec = signup_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    note = await action.on_skill_activate(
        "signup_interview",
        user_message=_OPENING,
    )

    assert note is not None
    assert "message evaluation" in note.lower()


@pytest.mark.asyncio
async def test_activation_eval_then_set_field_then_next_question(signup_action):
    action, spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance=_OPENING)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    await action._handle_start("signup_interview", visitor, user_message=_OPENING)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)
    assert prep.observations[0]["tool"] == "interview__message_evaluation"

    set_result = json.loads(
        await action._handle_set_field(
            field="user_name",
            value="Eldon Marks",
            visitor=visitor,
        )
    )
    assert set_result["ok"] is True
    assert set_result["fields"].get("user_name") == "Eldon Marks"
    assert "next_tool" not in set_result
    assert set_result["next_questions"][0]["name"] == "available_times"
    assert set_result["response_directive"].startswith("Tell the user:")


@pytest.mark.asyncio
async def test_set_field_idempotent_when_field_already_stored(signup_action):
    action, _spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance=_OPENING)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    await action._handle_start("signup_interview", visitor, user_message=_OPENING)

    first = json.loads(
        await action._handle_set_field(
            field="user_name",
            value="Eldon Marks",
            visitor=visitor,
        )
    )
    assert first["stored"] is True

    second = json.loads(
        await action._handle_set_field(
            field="user_name",
            value="Eldon Marks",
            visitor=visitor,
        )
    )
    assert second["ok"] is True
    assert second["stored"] is False
    assert second["already_stored"] is True
    assert second["fields"]["user_name"] == "Eldon Marks"
