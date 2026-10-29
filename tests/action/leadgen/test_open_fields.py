"""Tests for open_fields catalog and ask_next pending activation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.leadgen.engine import (
    _set_pending_activation,
    capture_state,
    handle_capture,
    open_fields,
)
from jvagent.action.leadgen.leadgen_action import LeadGenAction
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


def test_capture_state_has_no_next_pointers():
    state = capture_state(_Record({"full_name": "Ann"}, missing=[]), _project_spec())
    assert "next_ask" not in state
    assert "next_optional" not in state
    assert "next_project_optional" not in state
    assert "open_fields" in state
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
async def test_ask_next_stamps_pending_activation():
    updates = {}

    class Conv:
        async def update_context(self, payload):
            updates.update(payload)

    visitor = SimpleNamespace(conversation=Conv())
    await _set_pending_activation(
        visitor, ask_next="project_budget", spec=_project_spec()
    )
    assert updates["pending_activation"]["field"] == "project_budget"
    assert updates["pending_activation"]["skill"] == "t"
    assert updates["pending_activation"]["tool"] == "leadgen__capture"


@pytest.mark.asyncio
async def test_ask_next_omitted_clears_pending():
    updates = {}

    class Conv:
        async def update_context(self, payload):
            updates.update(payload)

    visitor = SimpleNamespace(conversation=Conv())
    await _set_pending_activation(visitor, ask_next=None, spec=_project_spec())
    assert updates["pending_activation"] is None


@pytest.mark.asyncio
async def test_handle_capture_ask_next_only():
    action = LeadGenAction()
    spec = _project_spec()
    action._registry._specs[spec.name] = spec

    class FakeRecord(_Record):
        async def update_yaml(self, updates):
            return False

        async def append_to_section(self, *a, **k):
            return True

    record = FakeRecord({}, missing=["full_name"])
    user = SimpleNamespace(user_id="u1", name=None)
    interaction = SimpleNamespace(channel="default")
    updates = {}

    class Conv:
        async def update_context(self, payload):
            updates.update(payload)

    visitor = SimpleNamespace(interaction=interaction, conversation=Conv())

    with (
        patch(
            "jvagent.action.leadgen.engine.get_user_and_interaction",
            return_value=(user, interaction),
        ),
        patch(
            "jvagent.action.leadgen.engine.LeadRecord.get_or_create_for_user",
            new=AsyncMock(return_value=record),
        ),
    ):
        result = json.loads(
            await handle_capture(
                action, skill="t", visitor=visitor, ask_next="full_name"
            )
        )

    assert result["status"] == "pending"
    assert result["ask_next"] == "full_name"
    assert "open_fields" in result
    assert updates["pending_activation"]["field"] == "full_name"


@pytest.mark.asyncio
async def test_handle_capture_rejects_unknown_ask_next():
    action = LeadGenAction()
    spec = _project_spec()
    action._registry._specs[spec.name] = spec
    user = SimpleNamespace(user_id="u1", name=None)
    interaction = SimpleNamespace(channel="default")
    visitor = SimpleNamespace(interaction=interaction)

    with patch(
        "jvagent.action.leadgen.engine.get_user_and_interaction",
        return_value=(user, interaction),
    ):
        result = json.loads(
            await handle_capture(
                action, skill="t", visitor=visitor, ask_next="not_a_field"
            )
        )

    assert result["status"] == "invalid-argument"
