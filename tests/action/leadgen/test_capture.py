"""Tests for leadgen capture engine (mocked persistence)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jvagent.action.leadgen.engine import canonicalize_fields, handle_capture
from jvagent.action.leadgen.leadgen_action import LeadGenAction
from jvagent.action.leadgen.spec import FieldDef, LeadGenSpec, SyncDef


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
    with patch("jvagent.action.leadgen.engine.get_dispatch_visitor") as gv:
        gv.return_value = SimpleNamespace(interaction=None)
        result = json.loads(await handle_capture(action, name="Jane"))
    assert "error" in result
