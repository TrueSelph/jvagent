"""Interview spec loads from SKILL.md frontmatter ``interview:`` block."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.interview_action.interview_loader import (
    InterviewRegistry,
    load_interview_spec,
    load_interview_spec_from_skill,
    parse_interview_spec,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures/skills/onboarding_interview"
_SIGNUP = (
    Path(__file__).resolve().parents[3]
    / "examples/jvagent_app/agents/jvagent/orchestrator_agent/skills/signup_interview"
)


def test_load_interview_spec_from_skill_fixture():
    spec = load_interview_spec_from_skill(_FIXTURES)
    assert spec is not None
    assert spec.name == "onboarding_interview"
    assert spec.get_question("phone_number") is not None
    assert spec.get_tool("send_otp") is not None
    assert spec.completion is not None
    assert spec.completion.function == "complete_onboarding"


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


def test_deprecated_interview_yaml_fallback(tmp_path, caplog):
    skill_dir = tmp_path / "legacy_interview"
    skill_dir.mkdir()
    (skill_dir / "interview.yaml").write_text(
        """
name: legacy_interview
title: Legacy
questions:
  - name: only
    question: "Only?"
    required: true
""",
        encoding="utf-8",
    )
    registry = InterviewRegistry()
    with caplog.at_level("WARNING"):
        registry.discover([str(tmp_path)])
    assert "deprecated" in caplog.text.lower()
    assert registry.get("legacy_interview") is not None


def test_frontmatter_preferred_over_yaml(tmp_path, caplog):
    skill_dir = tmp_path / "dual_interview"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: dual_interview
interview:
  title: From frontmatter
  questions:
    - name: fm_field
      question: "FM?"
      required: true
---
""",
        encoding="utf-8",
    )
    (skill_dir / "interview.yaml").write_text(
        """
name: dual_interview
title: From yaml
questions:
  - name: yaml_field
    question: "YAML?"
    required: true
""",
        encoding="utf-8",
    )
    registry = InterviewRegistry()
    with caplog.at_level("WARNING"):
        registry.discover([str(tmp_path)])
    spec = registry.get("dual_interview")
    assert spec is not None
    assert spec.questions[0].name == "fm_field"
    assert "deprecated" in caplog.text.lower()


def test_signup_frontmatter_matches_parse_interview_spec():
    from_skill = load_interview_spec_from_skill(_SIGNUP)
    assert from_skill is not None
    # name comes from SKILL.md frontmatter when omitted inside interview:
    assert from_skill.name == "signup_interview"
    assert len(from_skill.questions) == 4
    assert from_skill.get_tool("reset_signup_interview") is not None


@pytest.mark.parametrize(
    "field_name",
    ["user_name", "available_times", "user_email", "phone_number"],
)
def test_signup_question_names(field_name):
    spec = load_interview_spec_from_skill(_SIGNUP)
    assert spec is not None
    assert spec.get_question(field_name) is not None
