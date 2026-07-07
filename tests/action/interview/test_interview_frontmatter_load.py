"""Interview spec loads from SKILL.md frontmatter ``interview:`` block."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.interview.spec import (
    InterviewRegistry,
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR as _SIGNUP

_FIXTURES = Path(__file__).resolve().parent / "fixtures/skills/onboarding_interview"


def test_load_interview_spec_from_skill_fixture():
    spec = load_interview_spec_from_skill(_FIXTURES)
    assert spec is not None
    assert spec.name == "onboarding_interview"
    assert spec.get_field("phone_number") is not None
    assert spec.get_skill_tool("send_otp") is not None
    assert spec.handlers.complete == "complete_onboarding"
    assert spec.handlers.reset == "reset_onboarding"


def test_registry_discovers_frontmatter_skill(tmp_path):
    skill_dir = tmp_path / "demo_interview"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_interview
description: Demo
interview:
  title: Demo
  fields:
    - key: q1
      prompt: "Q?"
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
    assert spec.fields[0].key == "q1"


def test_signup_frontmatter_matches_parse_interview_spec():
    from_skill = load_interview_spec_from_skill(_SIGNUP)
    assert from_skill is not None
    assert from_skill.name == "signup_interview"
    assert len(from_skill.fields) == 6
    assert from_skill.confirm == "manual"


@pytest.mark.parametrize(
    "field_name",
    [
        "user_name",
        "available_times",
        "training_format",
        "user_email",
        "employer_name",
        "phone_number",
    ],
)
def test_signup_field_keys(field_name):
    spec = load_interview_spec_from_skill(_SIGNUP)
    assert spec is not None
    assert spec.get_field(field_name) is not None
