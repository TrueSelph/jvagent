"""Tests for CockpitRouter._build_active_tasks_section.

Regression coverage for the bug where the routing prompt's
``{active_tasks_section}`` placeholder was always empty, so the routing
LLM had no signal that an interview / multi-step flow was in progress.
Fragments ("Yes", "No", short answers) could then be misclassified or
routed to a parallel handler instead of back to the active owner.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jvagent.action.cockpit.routing.router import CockpitRouter


def _make_handle(
    task_id: str,
    owner_action: str,
    *,
    title: str = "",
    task_type: str = "INTERVIEW",
    state: str = "active",
):
    handle = MagicMock()
    handle.id = task_id
    handle.owner_action = owner_action
    handle.title = title
    handle.task_type = task_type
    handle.data = {"state": state, "interview_type": owner_action}
    return handle


def _make_router_with_tasks(active_tasks):
    """Build a CockpitRouter wired to a visitor whose tasks.list returns ``active_tasks``."""
    action = MagicMock()
    router = CockpitRouter(action)
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(return_value=active_tasks)
    router._visitor = visitor
    return router, visitor


def test_active_tasks_section_empty_when_no_visitor() -> None:
    action = MagicMock()
    router = CockpitRouter(action)
    router._visitor = None
    assert router._build_active_tasks_section() == ""


def test_active_tasks_section_empty_when_no_conversation() -> None:
    action = MagicMock()
    router = CockpitRouter(action)
    visitor = MagicMock()
    visitor.conversation = None
    router._visitor = visitor
    assert router._build_active_tasks_section() == ""


def test_active_tasks_section_empty_when_list_empty() -> None:
    router, _ = _make_router_with_tasks([])
    assert router._build_active_tasks_section() == ""


def test_active_tasks_section_renders_owner_state_and_routing_rule() -> None:
    """Single active interview surfaces owner_action, task_type, state, and rule."""
    handle = _make_handle(
        "t-1", "ReportInterviewInteractAction", task_type="INTERVIEW", state="active"
    )
    router, _ = _make_router_with_tasks([handle])

    out = router._build_active_tasks_section()

    assert "ACTIVE TASKS" in out
    assert "owner_action=ReportInterviewInteractAction" in out
    assert "[INTERVIEW]" in out
    assert "(state: active)" in out
    assert "Routing rule" in out
    # Trailing blank line so it splices cleanly into the prompt template.
    assert out.endswith("\n\n")


def test_active_tasks_section_dedupes_owner_action() -> None:
    """Multiple tasks under the same owner collapse to one entry to keep prompt clean."""
    h1 = _make_handle("t-1", "ReportInterviewInteractAction", state="active")
    h2 = _make_handle("t-2", "ReportInterviewInteractAction", state="review")
    router, _ = _make_router_with_tasks([h1, h2])

    out = router._build_active_tasks_section()

    # Only the first handle wins (dedup).
    assert out.count("owner_action=ReportInterviewInteractAction") == 1
    # First handle's state was "active" — kept.
    assert "(state: active)" in out
    assert "(state: review)" not in out


def test_active_tasks_section_renders_multiple_owners() -> None:
    """Distinct owner_action entries each get their own line."""
    h1 = _make_handle("t-1", "ReportInterviewInteractAction", state="active")
    h2 = _make_handle("t-2", "FeedbackInterviewInteractAction", state="active")
    router, _ = _make_router_with_tasks([h1, h2])

    out = router._build_active_tasks_section()

    assert "owner_action=ReportInterviewInteractAction" in out
    assert "owner_action=FeedbackInterviewInteractAction" in out


def test_active_tasks_section_handles_missing_owner_action() -> None:
    """Tasks with no owner_action surface as ``(unspecified)`` rather than crashing."""
    handle = _make_handle("t-1", "", state="active")
    router, _ = _make_router_with_tasks([handle])

    out = router._build_active_tasks_section()

    assert "owner_action=(unspecified)" in out


def test_active_tasks_section_swallows_list_exceptions() -> None:
    """If ``visitor.tasks.list`` raises, render an empty section (don't crash routing)."""
    action = MagicMock()
    router = CockpitRouter(action)
    visitor = MagicMock()
    visitor.conversation = MagicMock()
    visitor.tasks = MagicMock()
    visitor.tasks.list = MagicMock(side_effect=RuntimeError("boom"))
    router._visitor = visitor

    assert router._build_active_tasks_section() == ""
