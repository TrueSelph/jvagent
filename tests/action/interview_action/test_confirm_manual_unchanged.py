"""Default/manual confirm still uses review_confirmation_directive."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import parse_interview_spec


@pytest.mark.asyncio
async def test_default_review_manual_waits_for_confirm(tmp_path):
    spec = parse_interview_spec(
        {
            "title": "Manual demo",
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
        default_name="manual_demo",
    )
    assert spec.confirm == "manual"

    action = InterviewAction()
    action._registry._specs["manual_demo"] = spec
    session = InterviewSession(interview_type="manual_demo")
    session.set_value("name", "Jane Doe")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    result = json.loads(await action._handle_review())
    assert result["ok"] is True
    assert result.get("confirm", "manual") == "manual"
    assert result.get("next_tool") != "interview__complete"
    directive = result["response_directive"]
    assert "explicitly confirm" in directive.lower() or "confirm" in directive.lower()
    assert "Do NOT call interview__complete until" in directive


@pytest.mark.asyncio
async def test_get_status_includes_confirm(tmp_path):
    spec = parse_interview_spec(
        {
            "title": "Status demo",
            "confirm": "auto",
            "fields": [],
        },
        source_dir=str(tmp_path),
        default_name="status_demo",
    )
    action = InterviewAction()
    action._registry._specs["status_demo"] = spec
    session = InterviewSession(interview_type="status_demo")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))

    result = json.loads(await action._handle_get_status())
    assert result["confirm"] == "auto"
