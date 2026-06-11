"""Interview skill contract validation."""

from jvagent.action.interview._validate_contract import (
    validate_interview_skill_dir,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


def test_signup_interview_contract_validates():
    ok, issues = validate_interview_skill_dir(SIGNUP_INTERVIEW_SKILL_DIR)
    assert ok is True
    assert issues == []
