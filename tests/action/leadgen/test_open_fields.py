"""Tests for open_fields/optional_fields, capture state, and field validation."""

from __future__ import annotations

import pytest

from jvagent.action.leadgen.engine import (
    capture_state,
    open_fields,
    optional_fields,
    validate_fields,
)
from jvagent.action.leadgen.spec import (
    FieldDef,
    LeadGenSpec,
    SyncDef,
    fields_reference,
    parse_leadgen_spec,
)


class _Record:
    def __init__(self, data, missing=None):
        self._data = dict(data)
        self._missing = list(missing or [])

    def get_yaml(self):
        return dict(self._data)

    def get_missing_fields(self):
        return list(self._missing)


def _project_spec():
    return LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="full_name", required=True),
            FieldDef(key="project_description"),
            FieldDef(key="project_location"),
            FieldDef(key="contact_role"),
            FieldDef(
                key="project_budget",
                relevant_when={"any_present": ["project_description"]},
            ),
            FieldDef(
                key="project_timeline",
                relevant_when={"any_present": ["project_description"]},
            ),
        ],
        sync=SyncDef(mode="manual"),
    )


def test_open_fields_includes_empty_required_and_optional():
    fields = {"project_location": "Region 5"}
    keys = [e["key"] for e in open_fields(fields, _project_spec())]
    assert "full_name" in keys
    assert "contact_role" in keys
    assert "project_description" in keys
    assert "project_budget" not in keys
    assert "project_timeline" not in keys
    assert "project_location" not in keys


def test_open_fields_project_gate_opens_with_description():
    fields = {
        "full_name": "Ann",
        "project_description": "sea wall",
        "contact_role": "Engineer",
    }
    keys = [e["key"] for e in open_fields(fields, _project_spec())]
    assert "project_budget" in keys
    assert "project_timeline" in keys
    assert "full_name" not in keys


def test_optional_fields_excludes_required_fields():
    fields = {"project_location": "Region 5"}
    keys = [e["key"] for e in optional_fields(fields, _project_spec())]
    assert "full_name" not in keys
    assert "contact_role" in keys
    assert "project_budget" not in keys


def test_capture_state_has_no_next_pointers():
    state = capture_state(_Record({"full_name": "Ann"}, missing=[]), _project_spec())
    assert "next_ask" not in state
    assert "next_optional" not in state
    assert "next_project_optional" not in state
    assert "ask_next" not in state
    assert "optional_fields" in state
    assert state["missing_fields"] == []


def test_parse_relevant_when():
    spec = parse_leadgen_spec(
        {
            "fields": [
                {"key": "project_description"},
                {
                    "key": "project_budget",
                    "relevant_when": {"any_present": ["project_description"]},
                },
            ],
            "gap_fill": {"priority": ["project_description"]},
        },
        source_dir="/tmp",
        default_name="s",
    )
    f = spec.get_field("project_budget")
    assert f is not None
    assert f.relevant_when == {"any_present": ["project_description"]}
    ref = next(e for e in fields_reference(spec) if e["key"] == "project_budget")
    assert ref["relevant_when"]["any_present"] == ["project_description"]


def test_parse_rejects_qualification_queues():
    with pytest.raises(ValueError, match="qualification"):
        parse_leadgen_spec(
            {
                "fields": [{"key": "a"}],
                "gap_fill": {"qualification": ["a"]},
            },
            source_dir="/tmp",
            default_name="s",
        )


@pytest.mark.asyncio
async def test_validate_fields_keeps_valid_and_reports_errors():
    spec = LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="full_name", validator="person_name"),
            FieldDef(key="email", validator="email"),
        ],
    )
    validated, errors = await validate_fields(
        {"full_name": "Ann", "email": "bad"}, spec
    )
    assert validated["full_name"] == "Ann"
    assert "email" not in validated
    assert "email" in errors


@pytest.mark.asyncio
async def test_validate_fields_accepts_decline_value():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="email", validator="email", decline_value="N/A")],
    )
    validated, errors = await validate_fields({"email": "N/A"}, spec)
    assert validated["email"] == "N/A"
    assert errors == {}
