"""Tests for input_handler hook in set_field pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.interview_loader import load_interview_spec
from jvagent.action.interview_action.session import InterviewSession


@pytest.mark.asyncio
async def test_input_handler_runs_before_validator(tmp_path):
    skill_dir = tmp_path / "handler_demo"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (skill_dir / "interview.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "handler_demo",
                "questions": [
                    {
                        "name": "slot",
                        "question": "Pick a slot",
                        "required": True,
                        "input_handler": "normalize_slot",
                        "validator": {"function": "text"},
                    }
                ],
            }
        ),
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
    spec = load_interview_spec(str(skill_dir / "interview.yaml"))
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
