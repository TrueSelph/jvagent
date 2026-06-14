"""field_reference serialization: full ordered field metadata."""

from __future__ import annotations

from jvagent.action.interview.spec import (
    fields_reference,
    load_interview_spec_from_skill,
)
from tests.action.interview.conftest import SIGNUP_INTERVIEW_SKILL_DIR


def test_fields_reference_lists_all_fields_in_order():
    spec = load_interview_spec_from_skill(SIGNUP_INTERVIEW_SKILL_DIR)
    ref = fields_reference(spec)

    assert [f["key"] for f in ref] == spec.field_keys()
    first = ref[0]
    assert "key" in first and "prompt" in first
    assert all("prompt" in entry for entry in ref)
