"""Idempotency guard: re-submitting an already-stored field is a no-op.

The thin harness lets the model own extraction, so a model may redundantly
re-submit a field it already collected on an earlier turn. `handle_set_fields`
must short-circuit such a re-submit — skipping the pre/validator/post processors
so their side effects (e.g. API lookups in post_processors) do not re-fire —
while still treating a genuine value change as a correction.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.fixture
def signup_action():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec
    return action, spec


async def _start(action):
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, utterance="My name is Eldon Marks")
    action._save_session = AsyncMock()
    action._ensure_active_task = AsyncMock()
    await action._handle_start(
        "signup_interview", visitor, user_message="My name is Eldon Marks"
    )
    return visitor


@pytest.mark.asyncio
async def test_resubmit_same_value_is_idempotent_noop(signup_action):
    action, _spec = signup_action
    visitor = await _start(action)

    first = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"}, visitor=visitor
        )
    )
    assert first["results"][0]["stored"] is True
    assert not first["results"][0].get("idempotent")

    second = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Eldon Marks"}, visitor=visitor
        )
    )
    assert second["ok"] is True
    assert second["results"][0]["stored"] is True
    assert second["results"][0]["idempotent"] is True
    # Still steers the model forward rather than dead-ending.
    assert second.get("next_tool")


@pytest.mark.asyncio
async def test_changed_value_is_not_idempotent(signup_action):
    action, _spec = signup_action
    visitor = await _start(action)

    await action._handle_set_fields(
        fields={"user_name": "Eldon Marks"}, visitor=visitor
    )
    changed = json.loads(
        await action._handle_set_fields(
            fields={"user_name": "Jane Doe"}, visitor=visitor
        )
    )
    assert changed["results"][0]["stored"] is True
    assert not changed["results"][0].get("idempotent")
