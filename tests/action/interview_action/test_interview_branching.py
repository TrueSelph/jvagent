"""Tests for branch-aware question path resolution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from jvagent.action.interview_action.interview_action import InterviewAction
from jvagent.action.interview_action.interview_loader import load_interview_spec
from jvagent.action.interview_action.runtime.path_resolver import (
    compute_reachable_question_names,
    resolve_next_question_name,
)
from jvagent.action.interview_action.session import InterviewSession

_FIXTURES = Path(__file__).resolve().parent / "fixtures/skills"


@pytest.fixture
def branching_spec(tmp_path):
    skill_dir = tmp_path / "branch_demo"
    skill_dir.mkdir()
    spec_data = {
        "name": "branch_demo",
        "questions": [
            {
                "name": "user_type",
                "question": "Premium or standard?",
                "required": True,
                "validator": {"function": "text"},
                "branches": [
                    {
                        "condition": {"op": "equals", "value": "premium"},
                        "target": "premium_q",
                    },
                    {
                        "condition": {"op": "equals", "value": "standard"},
                        "target": "standard_q",
                    },
                ],
                "default_next": "contact",
            },
            {
                "name": "premium_q",
                "question": "Premium features?",
                "required": True,
                "default_next": "contact",
            },
            {
                "name": "standard_q",
                "question": "Standard setup?",
                "required": True,
                "default_next": "contact",
            },
            {"name": "contact", "question": "Contact info?", "required": True},
        ],
    }
    yaml_path = skill_dir / "interview.yaml"
    yaml_path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return load_interview_spec(str(yaml_path))


@pytest.mark.asyncio
async def test_branch_premium_path(branching_spec):
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "premium")
    load_fn = lambda _: None
    reachable = await compute_reachable_question_names(session, branching_spec, load_fn)
    assert reachable == ["user_type", "premium_q"]
    nxt = await resolve_next_question_name(session, branching_spec, load_fn)
    assert nxt == "premium_q"


@pytest.mark.asyncio
async def test_branch_standard_skips_premium(branching_spec):
    session = InterviewSession(interview_type="branch_demo")
    session.set_value("user_type", "standard")
    session.set_value("premium_q", "should prune")
    load_fn = lambda _: None
    reachable = await compute_reachable_question_names(session, branching_spec, load_fn)
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
        await action._handle_set_field(field="user_type", value="standard")
    )
    assert result["ok"] is True
    assert session.get_value("user_type") == "standard"
    assert session.get_value("standard_q") == "orphan"
    assert "premium_q" not in session.fields
