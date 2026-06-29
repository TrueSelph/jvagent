"""Tests for interview for_each per-item subpart expansion."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jvagent.action.interview.engine import build_review_summary, handle_set_fields
from jvagent.action.interview.flow import build_next_field, resolve_next_field_name
from jvagent.action.interview.for_each import (
    STATUS_ACTIVE,
    STATUS_COMPLETE,
    get_for_each_state,
)
from jvagent.action.interview.interview_action import InterviewAction
from jvagent.action.interview.session import InterviewSession
from jvagent.action.interview.spec import (
    load_interview_spec_from_skill,
    parse_interview_spec,
)

_EXAMPLE_DIR = (
    Path(__file__).resolve().parents[3]
    / "jvagent/action/interview/examples/example_for_each_interview"
)


@pytest.fixture
def for_each_spec():
    return load_interview_spec_from_skill(_EXAMPLE_DIR)


@pytest.fixture
def for_each_action(for_each_spec):
    action = InterviewAction(metadata={"agent_dir": str(_EXAMPLE_DIR.parent)})
    action._registry._specs[for_each_spec.name] = for_each_spec
    action._ensure_specs_loaded = AsyncMock()
    return action, for_each_spec


def _load_fn(spec):
    from jvagent.action.interview.hooks import load_hook_function

    return lambda name: load_hook_function(spec, name)


@pytest.mark.asyncio
async def test_for_each_parse_rejects_child_top_level_collision():
    with pytest.raises(ValueError, match="collides with top-level"):
        parse_interview_spec(
            {
                "fields": [
                    {"key": "title", "prompt": "t"},
                    {
                        "key": "items",
                        "prompt": "items",
                        "for_each": {
                            "fields": [{"key": "title", "prompt": "child title"}],
                        },
                    },
                ]
            },
            source_dir="test",
            default_name="t",
        )


@pytest.mark.asyncio
async def test_for_each_fields_reference_includes_subparts(for_each_spec):
    from jvagent.action.interview.spec import fields_reference

    ref = fields_reference(for_each_spec)
    parent = next(e for e in ref if e["key"] == "item_ids")
    assert "for_each" in parent
    assert len(parent["for_each"]["fields"]) == 3


@pytest.mark.asyncio
async def test_for_each_walk_three_items_two_required_subparts(for_each_action):
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    load = _load_fn(spec)

    await handle_set_fields(
        action, fields={"item_ids": "A, B, C"}, visitor=SimpleNamespace()
    )

    state = get_for_each_state(session, "item_ids")
    assert state["status"] == STATUS_ACTIVE
    assert len(state["items"]) == 3

    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "title"
    assert "For item 1 (A):" in nxt["prompt"]
    assert nxt["for_each"]["total"] == 3

    await handle_set_fields(
        action, fields={"title": "Widget A"}, visitor=SimpleNamespace()
    )
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "quantity"

    await handle_set_fields(action, fields={"quantity": "2"}, visitor=SimpleNamespace())
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "notes"

    await handle_set_fields(
        action, fields={"notes": "fragile"}, visitor=SimpleNamespace()
    )
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "title"
    assert "For item 2 (B):" in nxt["prompt"]

    await handle_set_fields(
        action, fields={"title": "Widget B", "quantity": "1"}, visitor=SimpleNamespace()
    )
    await action._handle_skip_field(field="notes", visitor=SimpleNamespace())

    await handle_set_fields(
        action, fields={"title": "Widget C", "quantity": "5"}, visitor=SimpleNamespace()
    )
    await action._handle_skip_field(field="notes", visitor=SimpleNamespace())

    state = get_for_each_state(session, "item_ids")
    assert state["status"] == STATUS_COMPLETE
    assert len(state["records"]) == 3
    assert state["records"][0]["fields"]["title"] == "Widget A"
    assert state["records"][1]["item_id"] == "B"
    assert "notes" in state["records"][1]["skipped_fields"]

    nxt_name = await resolve_next_field_name(session, spec, load)
    assert nxt_name is None


@pytest.mark.asyncio
async def test_for_each_expand_skip_bypasses_subparts(for_each_action, for_each_spec):
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    session.set_value("item_ids", "solo")
    session.context["for_each"] = {
        "item_ids": {
            "status": "skipped",
            "items": [],
            "records": [],
            "child_keys": ["title", "quantity", "notes"],
        }
    }
    load = _load_fn(spec)
    nxt = await resolve_next_field_name(session, spec, load)
    assert nxt is None


@pytest.mark.asyncio
async def test_for_each_parent_correction_resets_records(for_each_action):
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    await handle_set_fields(
        action, fields={"item_ids": "A, B"}, visitor=SimpleNamespace()
    )
    await handle_set_fields(
        action, fields={"title": "One", "quantity": "1"}, visitor=SimpleNamespace()
    )
    await action._handle_skip_field(field="notes", visitor=SimpleNamespace())

    assert len(get_for_each_state(session, "item_ids")["records"]) == 1

    await handle_set_fields(action, fields={"item_ids": "X"}, visitor=SimpleNamespace())
    state = get_for_each_state(session, "item_ids")
    assert state["status"] == STATUS_ACTIVE
    assert len(state["items"]) == 1
    assert state["records"] == []

    nxt = await build_next_field(session, spec, _load_fn(spec))
    assert nxt["key"] == "title"
    assert "X" in nxt["prompt"]


@pytest.mark.asyncio
async def test_for_each_review_summary_groups_records(for_each_spec):
    session = InterviewSession(interview_type=for_each_spec.name)
    session.set_value("item_ids", "A, B")
    session.context["for_each"] = {
        "item_ids": {
            "status": "complete",
            "items": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
            "current_index": 2,
            "child_keys": ["title", "quantity", "notes"],
            "records": [
                {
                    "item_id": "A",
                    "label": "A",
                    "fields": {"title": "Alpha", "quantity": "1"},
                    "skipped_fields": ["notes"],
                },
                {
                    "item_id": "B",
                    "label": "B",
                    "fields": {"title": "Beta", "quantity": "2"},
                    "skipped_fields": [],
                },
            ],
        }
    }
    summary = build_review_summary(
        session,
        for_each_spec,
        session.get_collected_summary(),
        visible_keys=["item_ids"],
    )
    assert "Item Ids — A" in summary or "Item Ids — A" in summary.replace("_", " ")
    assert "Alpha" in summary
    assert "Beta" in summary


@pytest.mark.asyncio
async def test_for_each_child_store_inlines_next_prompt(for_each_action):
    """After storing a subpart field, set_fields must inline the next question."""
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    await handle_set_fields(action, fields={"item_ids": "A"}, visitor=SimpleNamespace())
    raw = await handle_set_fields(
        action, fields={"title": "Widget A"}, visitor=SimpleNamespace()
    )
    payload = json.loads(raw)
    assert payload["next_field_key"] == "quantity"
    assert "how many units" in payload["response_directive"].lower()
    assert payload.get("next_tool") is None


@pytest.mark.asyncio
async def test_for_each_parent_correction_preserves_state_on_validation_failure(
    for_each_action,
):
    """Submitting an invalid new value must NOT wipe the existing for_each expansion.

    Before the fix, wipe_parent_for_each fired before validation. A failed
    validation left the parent's old stored value intact but the expansion gone,
    permanently blocking the for_each children.
    """
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    # Store a valid parent value — expansion initialises to ACTIVE.
    await handle_set_fields(
        action, fields={"item_ids": "A, B"}, visitor=SimpleNamespace()
    )
    state_before = get_for_each_state(session, "item_ids")
    assert state_before is not None
    assert state_before["status"] == STATUS_ACTIVE

    # Submit an invalid value (contains invalid char '!'). Validation rejects it.
    raw = await handle_set_fields(
        action, fields={"item_ids": "A!!INVALID"}, visitor=SimpleNamespace()
    )
    payload = json.loads(raw)
    assert payload["ok"] is False, "Expected validation failure"

    # The expansion state must be unchanged — old value still stored, expansion intact.
    state_after = get_for_each_state(session, "item_ids")
    assert (
        state_after is not None
    ), "for_each state must not be wiped on validation failure"
    assert state_after["status"] == STATUS_ACTIVE
    assert session.get_value("item_ids") == "A, B"


@pytest.mark.asyncio
async def test_default_expand_skips_when_post_processor_omits_expand(for_each_spec):
    """Parent with for_each but no expand payload defaults to skipped."""
    from jvagent.action.interview.for_each import (
        apply_default_expand_after_parent_store,
    )

    session = InterviewSession(interview_type=for_each_spec.name)
    session.set_value("item_ids", "A")
    apply_default_expand_after_parent_store(session, for_each_spec, "item_ids")
    state = get_for_each_state(session, "item_ids")
    assert state["status"] == "skipped"
