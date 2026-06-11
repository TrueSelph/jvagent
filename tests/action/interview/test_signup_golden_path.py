"""Golden-path signup interview — activation through complete."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import load_session
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    action._ensure_active_task = AsyncMock()
    action._close_task = AsyncMock()
    return action, spec


@pytest.mark.asyncio
async def test_signup_golden_path_activation_to_complete(signup_action):
    action, spec = signup_action
    conversation = SimpleNamespace(context={}, save=AsyncMock())
    visitor = SimpleNamespace(
        conversation=conversation,
        tasks=SimpleNamespace(),
        utterance="Hello my name is Jane Doe",
    )
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()

    await action.on_skill_activate(
        "signup_interview",
        visitor,
        user_message=visitor.utterance,
    )
    session = load_session(conversation)
    assert session is not None
    assert session.interview_type == "signup_interview"

    name_result = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"}, visitor=visitor
        )
    )
    assert name_result["ok"] is True

    visitor.utterance = "Monday at 9"
    times_result = json.loads(
        await action._handle_set_fields(
            fields={"available_times": "Monday at 9"}, visitor=visitor
        )
    )
    assert times_result["ok"] is True

    visitor.utterance = "jane@gmail.com"
    email_result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "jane@gmail.com"}, visitor=visitor
        )
    )
    assert email_result["ok"] is True

    visitor.utterance = "no thanks"
    skip_result = json.loads(
        await action._handle_skip_field(field="phone_number", visitor=visitor)
    )
    assert skip_result["ok"] is True

    visitor.utterance = "looks good"
    review_result = json.loads(await action._handle_review(visitor=visitor))
    assert review_result["ok"] is True
    assert review_result["status"] == "review"

    complete_result = json.loads(await action._handle_complete(visitor=visitor))
    assert complete_result["ok"] is True
    assert complete_result["status"] == "completed"
    assert load_session(conversation) is None
