"""Review-before-complete gate + clean terminal skip (regression).

Reproduces the 'Skip' smoke-test failure: a model that skipped the last optional
field then jumped past the review confirmation — either looping on a skip/
next_field thrash or completing the task outright (dropping the turn-lock without
the user ever confirming the summary).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import load_session, save_session
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
    action._ensure_active_task = AsyncMock()
    action._close_task = AsyncMock()
    return action, spec


async def _activate(action):
    conversation = SimpleNamespace(context={}, save=AsyncMock())
    visitor = SimpleNamespace(
        conversation=conversation, tasks=SimpleNamespace(), utterance="sign me up"
    )

    async def _persist(session, _visitor=None):
        await save_session(conversation, session)

    action._save_session = _persist
    action._ensure_active_task = AsyncMock()
    await action.on_skill_activate(
        "signup_interview", visitor, user_message=visitor.utterance
    )
    return visitor


@pytest.mark.asyncio
async def test_complete_blocked_before_review_under_manual_confirm(signup_action):
    """interview__complete must not finalize a manual-confirm interview that never
    reached the review step — it routes back to interview__review instead."""
    action, spec = signup_action
    assert spec.confirm == "manual"
    visitor = await _activate(action)

    # Session is ACTIVE (no review yet). Jumping to complete is refused.
    result = json.loads(await action._handle_complete(visitor=visitor))

    assert result["ok"] is False
    assert result["status"] != "completed"
    assert result["next_tool"] == "interview__review"
    assert "interview__review" in result["response_directive"]


@pytest.mark.asyncio
async def test_complete_allowed_after_review(signup_action):
    """Once interview__review has run (status REVIEW), complete proceeds."""
    action, spec = signup_action
    visitor = await _activate(action)

    await action._handle_review(visitor=visitor)
    result = json.loads(await action._handle_complete(visitor=visitor))

    assert result["ok"] is True
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_same_turn_review_complete_blocked_under_manual_confirm(signup_action):
    """A model chaining review -> complete inside ONE interaction is confirming on
    the user's behalf — the summary was never seen. Blocked on the same
    interaction; allowed on the next one (user replied to the summary)."""
    action, spec = signup_action
    visitor = await _activate(action)
    visitor.interaction = SimpleNamespace(id="turn-1")

    await action._handle_review(visitor=visitor)
    result = json.loads(await action._handle_complete(visitor=visitor))
    assert result["ok"] is False
    assert result["status"] != "completed"
    assert "confirmation" in result["response_directive"].lower()

    # Next interaction (the user has now seen and answered the summary).
    visitor.interaction = SimpleNamespace(id="turn-2")
    result = json.loads(await action._handle_complete(visitor=visitor))
    assert result["ok"] is True
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_skip_unknown_field_key_is_rejected_no_phantom_skip(signup_action):
    """A skip with a key the spec doesn't define (a model guess from the prompt
    text) must NOT record a phantom skip — it re-anchors on the real pending
    field and leaves skipped_fields untouched."""
    action, spec = signup_action
    visitor = await _activate(action)

    result = json.loads(
        await action._handle_skip_field(
            field="training_availability_slot", visitor=visitor
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "UNKNOWN_FIELD"
    # Re-anchored on the first real pending field (required user_name).
    assert result["next_field"]["key"] == "user_name"
    # No phantom key leaked into skipped_fields.
    session = load_session(visitor.conversation)
    assert "training_availability_slot" not in session.skipped_fields
    assert session.skipped_fields == set()


@pytest.mark.asyncio
async def test_bare_skip_with_empty_queue_routes_to_review_not_error(signup_action):
    """A skip with no pending field is terminal, not an error — it nudges review
    rather than feeding a skip/next_field thrash loop."""
    action, spec = signup_action
    visitor = await _activate(action)

    # Force an empty pending queue: mark every field collected/skipped so
    # build_next_field returns nothing, then issue a bare skip.
    async def _no_next(*_args, **_kwargs):
        return None

    from jvagent.action.interview import engine

    orig = engine.build_next_field
    engine.build_next_field = _no_next
    try:
        result = json.loads(await action._handle_skip_field(field="", visitor=visitor))
    finally:
        engine.build_next_field = orig

    assert result["ok"] is True
    assert result["next_tool"] == "interview__review"
    assert "interview__review" in result["response_directive"]
