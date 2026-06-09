"""Legacy interview frontmatter keys must fail at parse time."""

from __future__ import annotations

import pytest

from jvagent.action.interview_action.core.interview_loader import parse_interview_spec


@pytest.mark.parametrize(
    "data,match",
    [
        ({"questions": []}, "questions"),
        ({"fields": [], "extractors": []}, "extractors"),
        ({"fields": [], "tools": []}, "tools"),
        ({"fields": [], "completion": {"function": "noop"}}, "completion"),
        (
            {
                "fields": [
                    {
                        "key": "x",
                        "prompt": "X?",
                        "pre_tools": ["fn"],
                    }
                ]
            },
            "pre_tools",
        ),
        (
            {
                "fields": [
                    {
                        "key": "x",
                        "prompt": "X?",
                        "validator": {"function": "text"},
                    }
                ]
            },
            "Nested validator",
        ),
    ],
)
def test_parse_interview_spec_rejects_legacy_keys(data, match):
    with pytest.raises(ValueError, match=match):
        parse_interview_spec(data, source_dir="/tmp/skill")


def test_parse_interview_spec_rejects_invalid_confirm():
    with pytest.raises(ValueError, match="confirm must be"):
        parse_interview_spec(
            {"fields": [], "confirm": "maybe"},
            source_dir="/tmp/skill",
        )
