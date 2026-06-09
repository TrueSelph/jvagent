"""confirm: auto chains interview__complete from review response."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.core.interview_loader import parse_interview_spec
from jvagent.action.interview_action.core.session import InterviewSession
from jvagent.action.interview_action.interview_action import InterviewAction


@pytest.mark.asyncio
async def test_default_review_auto_confirm_chains_complete(tmp_path):
    spec = parse_interview_spec(
        {
            "title": "Auto demo",
            "confirm": "auto",
            "fields": [
                {
                    "key": "name",
                    "prompt": "Name?",
                    "required": True,
                    "validator": "text",
                }
            ],
        },
        source_dir=str(tmp_path),
        default_name="auto_demo",
    )
    action = InterviewAction()
    action._registry._specs["auto_demo"] = spec
    session = InterviewSession(interview_type="auto_demo")
    session.set_value("name", "Jane Doe")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(await action._handle_review())
    assert result["ok"] is True
    assert result["confirm"] == "auto"
    assert result["next_tool"] == "interview__complete"
    directive = result["response_directive"]
    assert "interview__complete" in directive
    assert "explicitly confirm" not in directive.lower()
    assert "does this look correct" not in directive.lower()
