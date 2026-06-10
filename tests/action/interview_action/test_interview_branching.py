"""Tests for branch-aware question path resolution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview_action.flow import (
    compute_collectible_path_names,
    resolve_next_field_name,
)
from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.session import InterviewSession
from jvagent.action.interview_action.spec import parse_interview_spec

_FIXTURES = Path(__file__).resolve().parent / "fixtures/skills"


@pytest.fixture
def branching_spec(tmp_path):
    skill_dir = tmp_path / "branch_demo"
    skill_dir.mkdir()
    spec_data = {
        "name": "branch_demo",
        "fields": [
            {
                "key": "user_type",
                "prompt": "Premium or standard?",
                "required": True,
                "validator": "text",
                "branches": [
                    {
                        "when": {"op": "equals", "value": "premium"},
                        "goto": "premium_q",
                    },
                    {
                        "when": {"op": "equals", "value": "standard"},
                        "goto": "standard_q",
                    },
                ],
                "else": "contact",
            },
            {
                "key": "premium_q",
                "prompt": "Premium features?",
                "required": True,
                "else": "contact",
            },
            {
                "key": "standard_q",
                "prompt": "Standard setup?",
                "required": True,
                "else": "contact",
            },
            {"key": "contact", "prompt": "Contact info?", "required": True},
        ],
    }
    return parse_interview_spec(
        spec_data, source_dir=str(skill_dir), default_name="branch_demo"
    )


@pytest.mark.asyncio
async def test_branch_premium_path(branching_spec):
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    load_fn = lambda _: None
    reachable = await compute_collectible_path_names(session, branching_spec, load_fn)
    assert reachable == ["user_type", "premium_q"]
    nxt = await resolve_next_field_name(session, branching_spec, load_fn)
    assert nxt == "premium_q"


@pytest.mark.asyncio
async def test_branch_standard_skips_premium(branching_spec):
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "standard")
    session.set_value("premium_q", "should prune")
    load_fn = lambda _: None
    reachable = await compute_collectible_path_names(session, branching_spec, load_fn)
    assert "premium_q" not in reachable
    assert "standard_q" in reachable


@pytest.mark.asyncio
async def test_set_field_prunes_unreachable(branching_spec):
    action = InterviewAction()
    action._registry._specs["branch_demo"] = branching_spec
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    session.set_value("premium_q", "old answer")
    session.set_value("standard_q", "orphan")
    action._get_session_and_contract = AsyncMock(return_value=(session, branching_spec))
    action._save_session = AsyncMock()

    result = json.loads(
        await action._handle_set_fields(fields={"user_type": "standard"})
    )
    assert result["ok"] is True
    assert session.get_value("user_type") == "standard"
    assert session.get_value("standard_q") == "orphan"
    assert "premium_q" not in session.fields
