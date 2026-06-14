"""A post_processor note must not stall the chain to review/complete.

When the last required field is stored and its post_processor returns a `note`
(with no further questions), the set_fields response must lead with a single,
unambiguous tool call (`Call interview__review.`) and carry the note as
system_message — NOT a "Tell the user … then call" reply, which models tend to
deliver and then stop on (skipping review), especially alongside a competing
first-turn intro directive.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import load_interview_spec_from_skill
from tests.action.interview.conftest import (
    ORCHESTRATOR_AGENT_DIR,
    SIGNUP_INTERVIEW_SKILL_DIR,
)


@pytest.mark.asyncio
async def test_note_on_final_field_chains_to_review_not_reply():
    action = InterviewAction(metadata={"agent_dir": str(ORCHESTRATOR_AGENT_DIR)})
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    action._registry._specs[spec.name] = spec

    # All required fields except the note-bearing user_email are already stored;
    # phone (optional) is skipped — so storing user_email leaves nothing to ask.
    session = InterviewSession(interview_type="signup_interview")
    for key, val in [
        ("user_name", "Eldon Marks"),
        ("available_times", "Monday 9am"),
        ("training_format", "virtual"),
        ("employer_name", "V75 Inc"),
    ]:
        session.set_value(key, val)
    session.skipped_fields.add("phone_number")

    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    visitor = SimpleNamespace(conversation=MagicMock(), utterance="eldon@mail.com")

    # @mail.com triggers append_work_email_note (returns a note).
    result = json.loads(
        await action._handle_set_fields(
            fields={"user_email": "eldon@mail.com"}, visitor=visitor
        )
    )

    assert result["ok"] is True
    assert result["next_tool"] == "interview__review"
    # Leads with the tool call, not a "Tell the user … then call" reply.
    assert result["response_directive"] == "Call interview__review."
    assert "tell the user" not in result["response_directive"].lower()
    # The note survives as model context.
    assert "work email" in (result.get("system_message") or "").lower()
