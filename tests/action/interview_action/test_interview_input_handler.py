"""Tests for input_handler hook in set_field pipeline."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    load_interview_spec_from_skill,
)
from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.interview_action import InterviewAction


@pytest.mark.asyncio
async def test_input_handler_runs_before_validator(tmp_path):
    skill_dir = tmp_path / "handler_demo"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: handler_demo
interview:
  title: Handler demo
  questions:
    - name: slot
      question: Pick a slot
      required: true
      input_handler: normalize_slot
      validator:
        function: text
---
""",
        encoding="utf-8",
    )
    (scripts / "custom_tools.py").write_text(
        """
def normalize_slot(value="", **kwargs):
    return value.strip().upper()

def validate_text(value="", **kwargs):
    import json
    return json.dumps({"valid": True, "value": value})
""",
        encoding="utf-8",
    )
    spec = load_interview_spec_from_skill(skill_dir)
    assert spec is not None
    action = InterviewAction()
    action._registry._specs["handler_demo"] = spec
    session = InterviewSession(interview_type="handler_demo")
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_field(field="slot", value="  monday am  ")
    )
    assert result["ok"] is True
    assert session.get_value("slot") == "MONDAY AM"
