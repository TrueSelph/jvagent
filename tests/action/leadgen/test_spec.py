"""Tests for leadgen spec parsing."""

from pathlib import Path

from jvagent.action.leadgen.spec import (
    load_leadgen_spec_from_skill,
    parse_leadgen_spec,
)

EXAMPLE_SKILL = (
    Path(__file__).resolve().parents[3]
    / "jvagent"
    / "action"
    / "leadgen"
    / "examples"
    / "example_leadgen"
)


def test_parse_leadgen_spec_fields():
    spec = parse_leadgen_spec(
        {
            "title": "Test",
            "fields": [
                {"key": "name", "required": True, "validator": "person_name"},
                {"key": "email", "decline_value": "N/A"},
            ],
            "sync": {"mode": "manual", "min_fields": ["name"]},
        },
        source_dir="/tmp",
        default_name="test_leads",
    )
    assert spec.get_required_fields() == ["name"]
    assert spec.sync.mode == "manual"
    assert spec.get_field("email").decline_value == "N/A"


def test_load_example_leadgen_skill():
    spec = load_leadgen_spec_from_skill(EXAMPLE_SKILL)
    assert spec is not None
    assert spec.name == "example_leadgen"
    assert "name" in spec.field_keys()
    assert spec.sync.mode == "on_capture"
