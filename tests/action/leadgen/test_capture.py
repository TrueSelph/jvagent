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


class _FakeLeadRecord:
    """In-memory LeadRecord stand-in for capture merge + fields return tests."""

    def __init__(self, required_fields: list[str] | None = None):
        self._data: dict = {}
        self._required = list(required_fields or ["name", "email"])
        self.missing_fields = ""
        self.score = 0
        self.enrichment_status = "none"

    def get_yaml(self) -> dict:
        return dict(self._data)

    async def set_yaml(self, data: dict) -> None:
        self._data = dict(data)

    def get_required_fields(self) -> list[str]:
        return list(self._required)

    def get_missing_fields(self) -> list[str]:
        missing = []
        for field in self._required:
            val = self._data.get(field)
            if val is None or (isinstance(val, str) and not str(val).strip()):
                missing.append(field)
        return missing

    async def update_yaml(self, updates: dict) -> bool:
        changed = False
        for key, value in updates.items():
            if self._data.get(key) != value:
                self._data[key] = value
                changed = True
        if changed:
            missing = self.get_missing_fields()
            self.missing_fields = ", ".join(missing)
            total = len(self._required)
            self.score = int(((total - len(missing)) / total) * 100) if total else 0
            self.enrichment_status = (
                "complete"
                if not missing
                else ("partial" if len(missing) < total else "none")
            )
        return changed

    async def append_to_section(self, category: str, text: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_capture_result_includes_merged_fields():
    """Sequential captures must return full LeadRecord in ``fields``, not only
    this-turn ``fields_saved`` — so post-capture gates see prior optionals
    (e.g. interested_products) after a later contact capture.
    """
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="quote_leads",
        fields=[
            FieldDef(key="name", required=True, validator="person_name"),
            FieldDef(
                key="email", required=True, validator="email", decline_value="N/A"
            ),
            FieldDef(key="phone", required=True),
            FieldDef(
                key="organization",
                required=True,
                decline_value="Personal",
            ),
            FieldDef(key="interested_products", required=False),
        ],
        gap_fill=GapFillDef(priority=["name", "email", "phone", "organization"]),
        sync=SyncDef(mode="manual"),
    )
    action._registry._specs[spec.name] = spec

    record = _FakeLeadRecord(required_fields=["name", "email", "phone", "organization"])
    user = SimpleNamespace(user_id="u1", name=None)
    interaction = SimpleNamespace(channel="default")
    visitor = SimpleNamespace(interaction=interaction)

    products = "Supply and laying of non-woven geotextile 400–600 g/m² — 2,760 m²"

    with (
        patch(
            "jvagent.action.leadgen.engine.get_user_and_interaction",
            return_value=(user, interaction),
        ),
        patch(
            "jvagent.action.leadgen.engine.LeadRecord.get_or_create_for_user",
            return_value=record,
        ),
        patch("jvagent.action.leadgen.engine._LAST_CAPTURE", {}),
    ):
        first = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                interested_products=products,
            )
        )
        second = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                name="Tharick Jairam",
                email="tharick@example.com",
                phone="+5926000000",
                organization="Personal",
            )
        )

    assert first["status"] == "updated"
    assert first["fields_saved"] == ["interested_products"]
    assert first["fields"]["interested_products"] == products

    assert second["status"] == "updated"
    assert set(second["fields_saved"]) == {
        "name",
        "email",
        "phone",
        "organization",
    }
    assert second["missing_fields"] == []
    assert second["next_ask"] is None
    assert second["fields"]["interested_products"] == products
    assert second["fields"]["name"] == "Tharick Jairam"
    assert second["fields"]["email"] == "tharick@example.com"


@pytest.mark.asyncio
async def test_deduplicated_capture_still_returns_the_record():
    """An identical capture inside the dedupe window must answer with the same
    snapshot as any other capture.

    The orchestrator's repeat guard nudges a repeated tool call once before it
    ends the turn, so the first repeat does reach the dedupe exit — and a bare
    ``{"status": "deduplicated"}`` leaves a post-capture gate blinder than the
    ``fields_saved``-only payload this snapshot was added to replace.
    """
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="quote_leads",
        fields=[
            FieldDef(key="name", required=True, validator="person_name"),
            FieldDef(key="interested_products", required=False),
        ],
        gap_fill=GapFillDef(priority=["name"]),
        sync=SyncDef(mode="manual"),
    )
    action._registry._specs[spec.name] = spec

    record = _FakeLeadRecord(required_fields=["name"])
    user = SimpleNamespace(user_id="u1", name=None)
    interaction = SimpleNamespace(channel="default")
    visitor = SimpleNamespace(interaction=interaction)

    with (
        patch(
            "jvagent.action.leadgen.engine.get_user_and_interaction",
            return_value=(user, interaction),
        ),
        patch(
            "jvagent.action.leadgen.engine.LeadRecord.get_or_create_for_user",
            return_value=record,
        ),
        patch("jvagent.action.leadgen.engine._LAST_CAPTURE", {}),
    ):
        await handle_capture(
            action, skill="quote_leads", visitor=visitor, interested_products="mesh"
        )
        written = json.loads(
            await handle_capture(
                action, skill="quote_leads", visitor=visitor, name="Ann"
            )
        )
        repeated = json.loads(
            await handle_capture(
                action, skill="quote_leads", visitor=visitor, name="Ann"
            )
        )

    assert repeated["status"] == "deduplicated"
    assert repeated["fields_saved"] == []
    # Every exit from handle_capture answers with the same keys, so a caller
    # never has to branch on which one it got.
    assert set(repeated) == set(written)
    assert repeated["fields"] == written["fields"]
    assert repeated["fields"]["interested_products"] == "mesh"
    assert repeated["missing_fields"] == []
    assert repeated["next_ask"] is None
