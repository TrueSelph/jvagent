"""Interview spec loads from SKILL.md frontmatter ``interview:`` block."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.interview_action.core.interview_loader import (
    InterviewRegistry,
    load_interview_spec_from_skill,
    parse_interview_spec,
)
from tests.action.interview_action.conftest import SIGNUP_INTERVIEW_SKILL_DIR as _SIGNUP

_FIXTURES = Path(__file__).resolve().parent / "fixtures/skills/onboarding_interview"


def test_load_interview_spec_from_skill_fixture():
    spec = load_interview_spec_from_skill(_FIXTURES)
    assert spec is not None
    assert spec.name == "onboarding_interview"
    assert spec.get_question("phone_number") is not None
    assert spec.get_tool("send_otp") is not None
    assert spec.completion is not None
    assert spec.completion.function == "complete_onboarding"
    assert spec.reset is not None
    assert spec.reset.function == "reset_onboarding"


def test_registry_discovers_frontmatter_skill(tmp_path):
    skill_dir = tmp_path / "demo_interview"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_interview
description: Demo
interview:
  title: Demo
  questions:
    - name: q1
      question: "Q?"
      required: true
---
# Demo
""",
        encoding="utf-8",
    )
    registry = InterviewRegistry()
    registry.discover([str(tmp_path)])
    assert registry.list_specs() == ["demo_interview"]
    spec = registry.get("demo_interview")
    assert spec is not None
    assert spec.questions[0].name == "q1"


def test_signup_frontmatter_matches_parse_interview_spec():
    from_skill = load_interview_spec_from_skill(_SIGNUP)
    assert from_skill is not None
    # name comes from SKILL.md frontmatter when omitted inside interview:
    assert from_skill.name == "signup_interview"
    assert len(from_skill.questions) == 4


@pytest.mark.parametrize(
    "field_name",
    ["user_name", "available_times", "user_email", "phone_number"],
)
def test_signup_question_names(field_name):
    spec = load_interview_spec_from_skill(_SIGNUP)
    assert spec is not None
    assert spec.get_question(field_name) is not None
