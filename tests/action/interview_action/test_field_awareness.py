"""field_awareness messages, interaction events, and envelope wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview_action.engine import record_field_awareness
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.responses import build_field_awareness_message
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import load_interview_spec_from_skill
from tests.action.interview_action.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


def test_build_field_awareness_message_primary_field():
    awaiting = [
        {
            "key": "user_name",
            "prompt": "What's your full name?",
            "required": True,
        }
    ]
    message = build_field_awareness_message(awaiting)
    assert message == (
        "Awaiting user input for 'user_name' field. " "Question: What's your full name?"
    )


def test_build_field_awareness_message_multiple_fields():
    awaiting = [
        {"key": "user_name", "prompt": "Name?"},
        {"key": "user_email", "prompt": "Email?"},
    ]
    message = build_field_awareness_message(awaiting)
    assert "'user_name'" in message
    assert "Also awaiting: 'user_email'." in message


@pytest.mark.asyncio
async def test_handle_start_records_field_awareness_event(signup_action):
    action, _spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    interaction = MagicMock()
    interaction.events = []
    interaction.add_event = MagicMock(return_value=True)
    interaction.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, interaction=interaction)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    result = json.loads(
        await action._handle_start("signup_interview", visitor, user_message="sign up")
    )

    assert "field_awareness" in result
    assert "user_name" in result["field_awareness"]
    interaction.add_event.assert_called_once()
    assert "user_name" in interaction.add_event.call_args[0][0]
    interaction.save.assert_awaited()


@pytest.mark.asyncio
async def test_on_skill_activate_prepends_field_awareness(signup_action):
    action, _spec = signup_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    note = await action.on_skill_activate("signup_interview", user_message="sign up")

    assert note.startswith("Awaiting user input for 'user_name' field.")
    assert '"awaiting_fields"' in note


@pytest.mark.asyncio
async def test_record_field_awareness_upserts_single_snapshot_per_interaction():
    interaction = MagicMock()
    interaction.events = [
        {
            "action_name": "InterviewAction",
            "content": (
                "Awaiting user input for 'user_name' field. "
                "Question: What's your full name?"
            ),
        }
    ]
    interaction.add_event = MagicMock(return_value=True)
    interaction.save = AsyncMock()
    visitor = SimpleNamespace(interaction=interaction)

    await record_field_awareness(
        visitor,
        "Awaiting user input for 'available_times' field. "
        "Question: What times are you available to train?",
    )

    assert len(interaction.events) == 1
    assert "available_times" in interaction.events[0]["content"]
    interaction.add_event.assert_not_called()
    interaction.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_fields_success_includes_field_awareness(signup_action):
    action, spec = signup_action
    session = InterviewSession(interview_type="signup_interview")
    interaction = MagicMock()
    interaction.events = []
    interaction.add_event = MagicMock(return_value=True)
    interaction.save = AsyncMock()
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=SimpleNamespace(interaction=interaction),
        )
    )

    assert result["ok"] is True
    assert "available_times" in result["field_awareness"]
