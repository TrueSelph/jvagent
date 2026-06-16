"""Snapshot/rehydrate hooks — a torn-down flow rebuilds from its task (ADR-0026 TP3)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import (
    InterviewSession,
    clear_interview_context,
    save_session,
)
from jvagent.action.interview.spec import (
    InterviewRegistry,
    load_interview_spec_from_skill,
)

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"


def _action():
    action = InterviewAction()
    action._registry = InterviewRegistry()
    action._registry._specs["pre_alert_interview"] = load_interview_spec_from_skill(
        _SKILLS_DIR / "pre_alert_interview"
    )
    return action


def _visitor():
    conv = MagicMock()
    conv.context = {}
    conv.save = AsyncMock()
    return SimpleNamespace(conversation=conv), conv


@pytest.mark.asyncio
async def test_snapshot_then_rehydrate_restores_fields():
    action = _action()
    visitor, conv = _visitor()

    # A live session with collected state.
    sess = InterviewSession(interview_type="pre_alert_interview")
    sess.set_value("tracking_number", "1Z999")
    await save_session(conv, sess)

    # Snapshot captures the runtime.
    snap = await action.snapshot_task_state("pre_alert_interview", visitor)
    assert snap["fields"]["tracking_number"] == "1Z999"
    assert snap["interview_type"] == "pre_alert_interview"

    # Tear the live session down (as a detour would).
    clear_interview_context(conv)
    assert await action._get_session(visitor) is None

    # Rehydrate rebuilds it instead of starting fresh.
    assert (
        await action.rehydrate_from_task("pre_alert_interview", snap, visitor) is True
    )
    restored = await action._get_session(visitor)
    assert restored is not None
    assert restored.get_value("tracking_number") == "1Z999"


@pytest.mark.asyncio
async def test_rehydrate_noop_when_live_session_present():
    action = _action()
    visitor, conv = _visitor()
    sess = InterviewSession(interview_type="pre_alert_interview")
    await save_session(conv, sess)
    # A live session exists ⇒ rehydrate is a no-op (don't clobber).
    assert (
        await action.rehydrate_from_task(
            "pre_alert_interview", {"interview_type": "pre_alert_interview"}, visitor
        )
        is False
    )


@pytest.mark.asyncio
async def test_snapshot_empty_without_matching_session():
    action = _action()
    visitor, _ = _visitor()
    assert await action.snapshot_task_state("pre_alert_interview", visitor) == {}
    assert await action.rehydrate_from_task("pre_alert_interview", {}, visitor) is False


@pytest.mark.asyncio
async def test_entry_directive_is_terminal_first_question():
    """ADR-0026: when a skill is entered as a pushed prerequisite, its entry
    directive is the first field's terminal 'Tell the user:' prompt — so the
    orchestrator ends the turn asking the user instead of letting the model
    fabricate the answer and race past the gate."""
    action = _action()
    visitor, conv = _visitor()
    sess = InterviewSession(interview_type="pre_alert_interview")
    await save_session(conv, sess)

    directive = await action.task_lock_entry_directive("pre_alert_interview", visitor)
    assert isinstance(directive, str) and directive.strip()
    assert directive.strip().lower().startswith("tell the user:")


@pytest.mark.asyncio
async def test_entry_directive_none_without_session():
    action = _action()
    visitor, _ = _visitor()
    assert (
        await action.task_lock_entry_directive("pre_alert_interview", visitor) is None
    )


@pytest.mark.asyncio
async def test_entry_directive_advances_past_autoresolved_chain(monkeypatch):
    """A field whose pre_processor auto-resolves it returns a tool-call chain
    ('Call interview__next_field()'); the entry directive must advance past that to
    the first real question, not leak the chain to the user (ADR-0026)."""
    import json as _json

    action = _action()
    visitor, conv = _visitor()
    sess = InterviewSession(interview_type="pre_alert_interview")
    await save_session(conv, sess)

    calls = {"n": 0}

    async def fake_next_field(_action, _visitor):
        calls["n"] += 1
        if calls["n"] == 1:
            return _json.dumps({"response_directive": "Call interview__next_field()."})
        return _json.dumps(
            {"response_directive": "Tell the user: What is your tracking number?"}
        )

    monkeypatch.setattr(
        "jvagent.action.interview.engine.handle_next_field", fake_next_field
    )

    directive = await action.task_lock_entry_directive("pre_alert_interview", visitor)
    assert directive.strip().lower().startswith("tell the user")
    assert "interview__next_field" not in directive
    assert calls["n"] == 2  # advanced once past the chain
