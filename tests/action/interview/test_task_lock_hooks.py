"""InterviewAction task-lock hooks consumed by the orchestrator (ADR-0034 L5).

The orchestrator decides WHEN a locked skill is being abandoned and asks the
bound action to apply its own policy through duck-typed hooks; these tests pin
the interview's implementation of that contract (moved here from the
orchestrator suite when the coupling was removed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.spec import InterviewSpec


def _visitor_with_context(ctx=None):
    conversation = MagicMock()
    conversation.context = dict(ctx or {})
    conversation.save = AsyncMock()
    visitor = MagicMock()
    visitor.conversation = conversation
    return visitor


class _FakeHandle:
    def __init__(self):
        self.parked = None
        self.cancelled = None

    async def park(self, *, snapshot=None, reason=""):
        self.parked = {"snapshot": snapshot, "reason": reason}

    async def cancel(self, *, reason=""):
        self.cancelled = {"reason": reason}


class _FakeTaskStore:
    def __init__(self, handle):
        self._handle = handle

    def list(self, *, status=None, owner_action=None):
        return [self._handle] if self._handle is not None else []


def _action_with_spec(spec: InterviewSpec) -> InterviewAction:
    action = InterviewAction()
    action._registry._specs[spec.name] = spec
    action._ensure_specs_loaded = AsyncMock()  # type: ignore[method-assign]
    return action


@pytest.mark.asyncio
async def test_task_lock_abandon_parks_and_snapshots():
    handle = _FakeHandle()
    spec = InterviewSpec(name="signup", title="Signup", on_abandon="park")
    v = _visitor_with_context(
        {
            "interview": {
                "interview_type": "signup",
                "status": "active",
                "fields": {"user_name": "Eldon"},
            }
        }
    )
    v.tasks = _FakeTaskStore(handle)

    applied = await _action_with_spec(spec).task_lock_abandon("signup", v)

    assert applied is True
    assert handle.parked is not None
    assert handle.parked["snapshot"]["fields"] == {"user_name": "Eldon"}
    # live interview scratch cleared
    assert "interview" not in v.conversation.context


@pytest.mark.asyncio
async def test_task_lock_abandon_cancels():
    handle = _FakeHandle()
    spec = InterviewSpec(name="otp", title="Verify", on_abandon="cancel")
    v = _visitor_with_context(
        {"interview": {"interview_type": "otp", "status": "active", "fields": {}}}
    )
    v.tasks = _FakeTaskStore(handle)

    applied = await _action_with_spec(spec).task_lock_abandon("otp", v)

    assert applied is True
    assert handle.cancelled is not None
    assert handle.parked is None
    assert "interview" not in v.conversation.context


@pytest.mark.asyncio
async def test_task_lock_abandon_returns_false_without_task_or_spec():
    spec = InterviewSpec(name="signup", title="Signup", on_abandon="park")
    v = _visitor_with_context()
    v.tasks = _FakeTaskStore(None)
    action = _action_with_spec(spec)

    assert await action.task_lock_abandon("signup", v) is False
    assert await action.task_lock_abandon("unknown_skill", v) is False


@pytest.mark.asyncio
async def test_task_lock_progress_count_and_title():
    spec = InterviewSpec(name="signup", title="Signup", on_abandon="park")
    action = _action_with_spec(spec)
    v = _visitor_with_context(
        {
            "interview": {
                "interview_type": "signup",
                "status": "active",
                "fields": {"user_name": "Eldon", "email": "e@x.io"},
            }
        }
    )
    assert await action.task_lock_progress_count("signup", v) == 2
    # A different skill's session does not count as this skill's progress.
    assert await action.task_lock_progress_count("otp", v) == 0
    assert await action.task_lock_progress_count("signup", _visitor_with_context()) == 0
    assert action.task_lock_title("signup") == "Signup"
    assert action.task_lock_title("nope") == ""


@pytest.mark.asyncio
async def test_clear_task_lock_session_hook_clears_live_session():
    spec = InterviewSpec(name="signup", title="Signup")
    action = _action_with_spec(spec)
    v = _visitor_with_context(
        {"interview": {"interview_type": "signup", "status": "active", "fields": {}}}
    )
    await action.clear_task_lock_session(v)
    assert "interview" not in v.conversation.context


def test_interview_registers_its_vocabulary_with_the_orchestrator():
    """Dependency inversion: the orchestrator reads generic keys plus whatever a
    plugin registers at load — never interview literals of its own."""
    from jvagent.action.orchestrator.constants import (
        is_untrusted_directive_source,
        task_completion_flags,
        task_lock_skill_keys,
    )

    assert "interview_complete" in task_completion_flags()
    assert "interview_type" in task_lock_skill_keys()
    assert is_untrusted_directive_source("interview__set_fields") is False
