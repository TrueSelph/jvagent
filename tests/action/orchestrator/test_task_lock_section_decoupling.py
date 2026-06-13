"""task_lock_section_text carries no interview-specific literals."""

from __future__ import annotations

from types import SimpleNamespace

from jvagent.action.orchestrator.skill_tasks import task_lock_section_text


def test_task_lock_section_has_no_interview_compound_rule():
    doc = SimpleNamespace(
        name="signup_interview",
        body="PROCEDURE BODY",
        requires_tools=("interview__set_fields", "reply"),
    )
    text = task_lock_section_text(doc)
    assert "PROCEDURE BODY" in text
    assert "Compound extraction rule" not in text
    assert "interview__set_fields" not in text
