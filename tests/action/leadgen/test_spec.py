"""Tests for leadgen spec parsing."""

from pathlib import Path

import pytest

from jvagent.action.leadgen.spec import (
    DEFAULT_CHANNEL_DEFAULTS,
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
    assert "full_name" in spec.field_keys()
    assert spec.sync.mode == "on_capture"
    assert spec.messages.handoff_team == "sales team"
    assert spec.sync.min_fields == ["full_name"]


def test_sync_defaults_are_empty_gates():
    spec = parse_leadgen_spec(
        {"fields": [{"key": "issue"}], "sync": {"mode": "on_capture"}},
        source_dir="/tmp",
        default_name="t",
    )
    assert spec.sync.min_fields == []
    assert spec.sync.require_any == []
    assert spec.sync.timezone == "UTC"


def test_messages_and_timezone_parse():
    spec = parse_leadgen_spec(
        {
            "messages": {
                "entity": "ticket",
                "handoff_team": "support",
                "contact_label": "help desk",
                "visitor": "customer",
            },
            "sync": {"timezone": "America/Guyana", "min_fields": ["issue"]},
            "fields": [{"key": "issue", "required": True}],
        },
        source_dir="/tmp",
        default_name="t",
    )
    assert spec.messages.entity == "ticket"
    assert spec.messages.handoff_team == "support"
    assert spec.sync.timezone == "America/Guyana"
    assert spec.sync.min_fields == ["issue"]


def test_channel_defaults_omitted_disables_autofill():
    spec = parse_leadgen_spec(
        {"fields": [{"key": "phone"}]},
        source_dir="/tmp",
        default_name="t",
    )
    assert spec.channel_defaults is None
    assert spec.resolved_channel_defaults() == {}
    assert DEFAULT_CHANNEL_DEFAULTS == {}


def test_channel_defaults_empty_disables_autofill():
    spec = parse_leadgen_spec(
        {"fields": [{"key": "phone"}], "channel_defaults": {}},
        source_dir="/tmp",
        default_name="t",
    )
    assert spec.channel_defaults == {}
    assert spec.resolved_channel_defaults() == {}


def test_channel_defaults_rejects_bad_source():
    with pytest.raises(ValueError, match="user_id or user_name"):
        parse_leadgen_spec(
            {
                "fields": [{"key": "phone"}],
                "channel_defaults": {"whatsapp": {"phone": "email"}},
            },
            source_dir="/tmp",
            default_name="t",
        )
