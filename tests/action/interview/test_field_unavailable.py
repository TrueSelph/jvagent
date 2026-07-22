"""ADR-0034 — interview__field_unavailable: park / cancel / relax + parked resume."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview import tasks as interview_tasks
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession, save_session
from jvagent.action.interview.spec import (
    InterviewRegistry,
    load_interview_spec_from_skill,
    parse_interview_spec,
)
from jvagent.memory.task_store import TaskStore

_SKILLS_DIR = Path(__file__).resolve().parent / "fixtures/skills"


def _pre_alert_action():
    action = InterviewAction()
    action._registry = InterviewRegistry()
    action._registry._specs["pre_alert_interview"] = load_interview_spec_from_skill(
        _SKILLS_DIR / "pre_alert_interview"
    )
    action._ensure_specs_loaded = AsyncMock()
    return action


def _inline_action(name: str, fields: list):
    action = InterviewAction()
    action._registry = InterviewRegistry()
    action._registry._specs[name] = parse_interview_spec(
        {"name": name, "fields": fields}, source_dir="", default_name=name
    )
    action._ensure_specs_loaded = AsyncMock()
    return action


def _visitor():
    conv = MagicMock()
    conv.context = {}
    conv.tasks = []
    conv.save = AsyncMock()
    visitor = SimpleNamespace(conversation=conv, tasks=TaskStore(conv))
    return visitor, conv


# --- park + resume roundtrip -------------------------------------------------


@pytest.mark.asyncio
async def test_park_sets_task_parked_clears_session_and_reports():
    action = _pre_alert_action()
    visitor, conv = _visitor()

    await action._handle_start("pre_alert_interview", visitor, user_message="")
    await action._handle_set_fields(
        fields={"description": "electronics"}, visitor=visitor
    )

    resp = json.loads(
        await action._handle_field_unavailable("tracking_number", visitor)
    )
    assert resp["status"] == "parked"
    # Live session released (turn-lock freed).
    assert await action._get_session(visitor) is None
    # Task parked with the collected state snapshotted.
    parked = visitor.tasks.list(status="parked", owner_action="pre_alert_interview")
    assert len(parked) == 1
    assert parked[0].snapshot["fields"]["description"] == "electronics"
    # Reply names what was saved and what is still needed.
    directive = resp["response_directive"].lower()
    assert "description" in directive and "tracking number" in directive


@pytest.mark.asyncio
async def test_parked_task_resumes_on_reactivation():
    action = _pre_alert_action()
    visitor, conv = _visitor()

    await action._handle_start("pre_alert_interview", visitor, user_message="")
    await action._handle_set_fields(
        fields={"description": "electronics"}, visitor=visitor
    )
    await action._handle_field_unavailable("tracking_number", visitor)
    assert await action._get_session(visitor) is None

    # User returns; the skill re-activates.
    start = json.loads(
        await action._handle_start("pre_alert_interview", visitor, user_message="")
    )
    # Session restored with prior state; resumes from the still-missing field.
    sess = await action._get_session(visitor)
    assert sess is not None
    assert sess.get_value("description") == "electronics"
    assert start.get("start_field") == "tracking_number"
    # Task reactivated (no longer parked).
    assert not visitor.tasks.list(status="parked", owner_action="pre_alert_interview")
    assert visitor.tasks.list(status="active", owner_action="pre_alert_interview")


# --- relax -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relax_skips_field_and_continues():
    action = _inline_action(
        "t_relax",
        [
            {"key": "a", "prompt": "A?", "required": True},
            {
                "key": "b",
                "prompt": "B?",
                "required": True,
                "on_unavailable": "relax",
                "relaxable": True,
            },
        ],
    )
    visitor, conv = _visitor()
    await save_session(conv, InterviewSession(interview_type="t_relax"))

    resp = json.loads(await action._handle_field_unavailable("b", visitor))
    assert resp["ok"] is True
    assert "b" in resp["skipped_fields"]
    # Session stays open (not parked/cancelled); a is still collectible.
    sess = await action._get_session(visitor)
    assert sess is not None and sess.is_skipped("b")


# --- cancel ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_closes_task_and_clears_session():
    action = _inline_action(
        "t_cancel",
        [
            {
                "key": "otp",
                "prompt": "Code?",
                "required": True,
                "on_unavailable": "cancel",
            }
        ],
    )
    visitor, conv = _visitor()
    await save_session(conv, InterviewSession(interview_type="t_cancel"))
    await interview_tasks.ensure_active_task(
        visitor, action._registry._specs["t_cancel"], "desc"
    )

    resp = json.loads(await action._handle_field_unavailable("otp", visitor))
    assert resp["status"] == "cancelled"
    assert await action._get_session(visitor) is None
    # No active or parked task lingers.
    assert not visitor.tasks.list(status="active", owner_action="t_cancel")
    assert not visitor.tasks.list(status="parked", owner_action="t_cancel")


# --- policy defaults ---------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pending_field_routes_to_review():
    action = _inline_action("t_done", [{"key": "a", "prompt": "A?", "required": False}])
    visitor, conv = _visitor()
    sess = InterviewSession(interview_type="t_done")
    sess.skip_field("a")
    await save_session(conv, sess)

    resp = json.loads(await action._handle_field_unavailable("", visitor))
    assert resp["ok"] is True
    assert resp.get("next_tool") == "interview__review"
