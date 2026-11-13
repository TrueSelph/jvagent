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
)
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
    action.sales_contact = "555-0100"
    merged = merge_spec_with_action_defaults(action, spec)
    assert merged.sync.sales_contact == "555-0100"


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
    out, unknown = canonicalize_fields({"full_name": "Jane", "nickname": "JJ"}, spec)
    assert out["name"] == "Jane"
    assert unknown == ["nickname"]


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
    assert "next_ask" not in second
    assert "next_optional" not in second
    assert "next_project_optional" not in second
    assert "must_ask" not in second
    assert "open_fields" not in second
    assert second["next_required"] is None
    assert second["optional_fields"] == []
    assert "no optional field is open" in second["directive"]
    assert "gap_fill_priority" not in second
    # The catalogue keeps filled fields, in declaration order, so the model can
    # correct a stored value without a second list.
    assert [e["key"] for e in second["field_reference"]] == [
        "name",
        "email",
        "phone",
        "organization",
        "interested_products",
    ]
    assert second["field_reference"][0]["required"] is True
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
    assert "next_ask" not in repeated
    assert "must_ask" not in repeated
    assert "open_fields" not in repeated
    assert repeated["next_required"] is None
    assert repeated["optional_fields"] == []
    assert "gap_fill_priority" not in repeated


@pytest.mark.asyncio
async def test_unknown_field_is_reported_and_known_values_are_kept():
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="quote_leads",
        fields=[FieldDef(key="name", required=True, validator="person_name")],
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
        result = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                name="Ann",
                nickname="AJ",
            )
        )

    assert result["status"] == "updated"
    assert result["fields"]["name"] == "Ann"
    assert "nickname" not in result["fields"]
    assert result["validation_errors"]["nickname"] == "unknown field"


@pytest.mark.asyncio
async def test_failed_handoff_names_configured_contact_only():
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="quote_leads",
        fields=[FieldDef(key="name", required=True, validator="person_name")],
        sync=SyncDef(
            mode="on_capture",
            min_fields=["name"],
            require_any=[],
            sales_contact="555-0199",
        ),
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
        result = json.loads(
            await handle_capture(
                action, skill="quote_leads", visitor=visitor, name="Ann"
            )
        )

    assert "555-0199" in result["directive"]
    assert "saved but not yet sent" in result["directive"]
    assert "592" not in result["directive"]


@pytest.mark.asyncio
async def test_handoff_sentence_returns_when_the_snapshot_changes():
    action = LeadGenAction()
    spec = LeadGenSpec(
        name="quote_leads",
        fields=[
            FieldDef(key="name", required=True, validator="person_name"),
            FieldDef(key="project_description"),
            FieldDef(key="note"),
        ],
        sync=SyncDef(mode="on_capture"),
    )
    action._registry._specs[spec.name] = spec
    record = _FakeLeadRecord(required_fields=["name"])
    user = SimpleNamespace(user_id="u1", name=None)
    interaction = SimpleNamespace(channel="default")
    visitor = SimpleNamespace(interaction=interaction)

    async def _synced(*_args, **_kwargs):
        return {"status": "sync-complete", "results": []}

    with (
        patch(
            "jvagent.action.leadgen.engine.get_user_and_interaction",
            return_value=(user, interaction),
        ),
        patch(
            "jvagent.action.leadgen.engine.LeadRecord.get_or_create_for_user",
            return_value=record,
        ),
        patch("jvagent.action.leadgen.engine.maybe_auto_sync", new=_synced),
        patch("jvagent.action.leadgen.engine._LAST_CAPTURE", {}),
    ):
        first = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                name="Ann",
                project_description="roof",
            )
        )
        second = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                note="call after 4",
            )
        )
        record._data.pop("project_description")
        third = json.loads(
            await handle_capture(
                action,
                skill="quote_leads",
                visitor=visitor,
                project_description="new job",
            )
        )

    assert "Hand-off is complete" in first["directive"]
    assert "do not repeat" not in first["directive"]
    assert "do not repeat" in second["directive"]
    assert "do not repeat" not in third["directive"]
    assert "Hand-off is complete" in third["directive"]
