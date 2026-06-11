"""Activation with inline answers — model-driven set_fields (no server prep steering)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import save_session
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import (
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
async def test_on_skill_activate_notes_skill_procedure(signup_action):
    action, _spec = signup_action
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    action._get_conversation = AsyncMock(return_value=None)

    note = await action.on_skill_activate(
        "signup_interview",
        user_message=_OPENING,
    )

    assert note is not None
    assert note.startswith("Awaiting user input for 'user_name' field.")
    _awareness, json_body = note.split("\n\n", 1)
    parsed = json.loads(json_body)
    assert parsed["ok"] is True
    assert parsed["interview_type"] == "signup_interview"
    assert "missing_required" in parsed
    assert parsed["awaiting_fields"][0]["key"] == "user_name"
    assert "field_definitions" not in parsed


@pytest.mark.asyncio
async def test_activation_set_fields_then_model_chains_next_field(signup_action):
    action, spec = signup_action
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance=_OPENING)

    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    await action._handle_start("signup_interview", visitor, user_message=_OPENING)

    prep = await action.prepare_locked_skill_turn("signup_interview", visitor)
    assert prep.runtime_ready is True
    assert not prep.observations

    set_result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=visitor,
        )
    )
    assert set_result["ok"] is True
    assert set_result["fields"].get("user_name") == "Eldon Marks"
    assert set_result.get("next_tool") == "interview__next_field"


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
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=visitor,
        )
    )
    assert first["stored"] is True

    second = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"},
            visitor=visitor,
        )
    )
    assert second["ok"] is True
    assert second["stored"] is True
    assert second["fields"]["user_name"] == "Eldon Marks"
