"""Tests for leadgen capture engine (mocked persistence)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jvagent.action.leadgen.engine import (
    canonicalize_fields,
    handle_capture,
    merge_spec_with_action_defaults,
    next_ask,
)
from jvagent.action.leadgen.leadgen_action import LeadGenAction
from jvagent.action.leadgen.spec import FieldDef, GapFillDef, LeadGenSpec, SyncDef


@pytest.fixture
def leadgen_action_with_spec():
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="test_leads",
        fields=[
            FieldDef(key="name", required=True, validator="person_name"),
            FieldDef(
                key="email", required=True, validator="email", decline_value="N/A"
            ),
        ],
        sync=SyncDef(mode="manual"),
    )
    action._registry._specs[spec.name] = spec
    return action, spec


def test_next_ask_follows_priority_order():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="name"), FieldDef(key="phone"), FieldDef(key="email")],
        gap_fill=GapFillDef(priority=["name", "phone", "email"]),
    )
    # phone outranks email in priority
    assert next_ask(spec, ["email", "phone"]) == "phone"
    assert next_ask(spec, ["email"]) == "email"


def test_next_ask_none_when_complete():
    spec = LeadGenSpec(name="t", gap_fill=GapFillDef(priority=["name"]))
    assert next_ask(spec, []) is None


def test_next_ask_falls_back_to_first_missing():
    spec = LeadGenSpec(name="t", gap_fill=GapFillDef(priority=["name"]))
    # field not in priority list → fall back to first missing
    assert next_ask(spec, ["budget"]) == "budget"


def test_action_sync_config_applies_when_skill_has_none():
    """Sync config on the action (agent.yaml) governs when the skill declares none."""
    action = LeadGenAction()
    action.sync_destinations = [
        {"server": "leadfile", "mode": "mcp", "tool": "write_file", "arguments": {}}
    ]
    action.sync_mode = "on_complete"
    action.sync_min_fields = ["name", "email"]
    action.sync_require_any = ["phone"]

    spec = LeadGenSpec(name="s")  # no sync block → default SyncDef, no destinations
    merged = merge_spec_with_action_defaults(action, spec)

    assert merged.sync.destinations[0]["server"] == "leadfile"
    assert merged.sync.mode == "on_complete"
    assert merged.sync.min_fields == ["name", "email"]
    assert merged.sync.require_any == ["phone"]


def test_skill_sync_wins_over_action_sync():
    """A skill that declares its own destinations keeps full control of sync."""
    action = LeadGenAction()
    action.sync_destinations = [
        {"server": "leadfile", "mode": "mcp", "tool": "write_file", "arguments": {}}
    ]
    spec = LeadGenSpec(
        name="s",
        sync=SyncDef(
            mode="manual",
            destinations=[{"server": "crm", "mode": "mcp", "tool": "push"}],
        ),
    )
    merged = merge_spec_with_action_defaults(action, spec)

    assert merged.sync.destinations[0]["server"] == "crm"
    assert merged.sync.mode == "manual"


def test_canonicalize_aliases():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="name", aliases=["full_name"])],
    )
    out = canonicalize_fields({"full_name": "Jane"}, spec)
    assert out["name"] == "Jane"


@pytest.mark.asyncio
async def test_capture_no_interaction(leadgen_action_with_spec):
    action, _ = leadgen_action_with_spec
    with patch("jvagent.action.leadgen.engine.get_tool_visitor") as gv:
        gv.return_value = SimpleNamespace(interaction=None)
        result = json.loads(await handle_capture(action, name="Jane"))
    assert "error" in result
