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
    assert "For the first item id A" in nxt["prompt"]
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
    assert "For the second item id B" in nxt["prompt"]

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
async def test_for_each_batch_skip_hook_applies_to_later_items(for_each_action):
    """A batch-wide ask-time skip hook must fire for every for_each item.

    Regression: when set_fields completes one item and advances to the next, the
    next item's optional child was surfaced without running its ask-time
    pre_processor, so an already-declined field ("no notes for any item") got
    re-asked once on the second item. The framework now runs the ask-time skip
    hook after each advance, so the decline sticks across all items — the second
    item's notes is skipped even though skip_field is never called for it.
    """
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    load = _load_fn(spec)

    await handle_set_fields(
        action, fields={"item_ids": "A, B"}, visitor=SimpleNamespace()
    )
    assert get_for_each_state(session, "item_ids")["status"] == STATUS_ACTIVE

    # Item A: the user declines notes for the whole batch in the same message.
    decliner = SimpleNamespace(utterance="no notes for any item")
    await handle_set_fields(
        action, fields={"title": "Widget A", "quantity": "2"}, visitor=decliner
    )
    # Notes for A skipped by the ask-time hook and the iteration advanced to B.
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "title"
    assert "For the second item id B" in nxt["prompt"]

    # Item B: only the required fields are supplied — NO skip_field call for
    # notes. The batch-wide decline must skip B's notes on the advance.
    await handle_set_fields(
        action, fields={"title": "Widget B", "quantity": "1"}, visitor=SimpleNamespace()
    )

    state = get_for_each_state(session, "item_ids")
    assert state["status"] == STATUS_COMPLETE
    assert len(state["records"]) == 2
    assert "notes" in state["records"][0]["skipped_fields"]
    assert "notes" in state["records"][1]["skipped_fields"]
    assert await resolve_next_field_name(session, spec, load) is None


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
    assert "For item id X:" not in nxt["prompt"]


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
    assert "Item Id — A" in summary
    assert "Alpha" in summary
    assert "Beta" in summary
    # Tight spacing: the record header and its first child are separated by a
    # single newline (not a blank line).
    assert "**Item Id — A**\n  **Title**: Alpha" in summary
    # Exactly one blank line between the two records.
    assert "Alpha" in summary
    a_block_end = summary.index("Alpha")
    b_block_start = summary.index("Item Id — B")
    between = summary[a_block_end:b_block_start]
    # After the last child line of record A, there should be exactly one blank
    # line (\n\n) before the record B header.
    assert "\n\n" in between
    assert between.count("\n\n") == 1


@pytest.mark.asyncio
async def test_for_each_review_summary_renders_when_parent_omitted(for_each_spec):
    """Regression: omitting the parent via omit_fields must NOT suppress the
    per-item for_each section.

    Before the fix, ``for_each_review_sections`` dropped the parent when it
    appeared in ``omit_fields`` (mirrored via ``omit_parents``), so a custom
    review handler that hides the parent's raw value also hid every per-item
    record — leaving an empty summary. The per-item section is the only place
    collected child values are surfaced, so it must always render.
    """
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
    # Simulate a custom review handler that omits the parent's raw line.
    summary = build_review_summary(
        session,
        for_each_spec,
        session.get_collected_summary(),
        visible_keys=["item_ids"],
        omit_fields={"item_ids"},
    )
    # The parent's raw value line is suppressed…
    assert "A, B" not in summary
    # …but every per-item record still renders.
    assert "Item Id — A" in summary
    assert "Alpha" in summary
    assert "Beta" in summary
    # Tight spacing within a record: header and first child separated by a single
    # newline (no blank line inside a record block).
    assert "**Item Id — A**\n  **Title**: Alpha" in summary
    # Exactly one blank line between records.
    a_end = summary.index("Alpha")
    b_start = summary.index("Item Id — B")
    assert summary[a_end:b_start].count("\n\n") == 1


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
async def test_for_each_staged_with_parent_in_same_set_fields_call(for_each_action):
    """for_each_staged data sent in the same set_fields call as the parent field
    that triggers the for_each expansion must be properly staged, not silently
    dropped.

    Previously, for_each_staged was processed BEFORE field storage and
    post_processors ran. Since the parent field's post_processor creates the
    for_each expansion, there was no active for_each at that point, and
    for_each_staged data was silently lost. The fix defers for_each_staged
    processing until after all fields have been stored and post_processors
    have run.
    """
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()

    # Send the parent field AND for_each_staged in the SAME set_fields call.
    # The parent field's post_processor (expand_item_ids) creates the for_each
    # expansion. The for_each_staged data must be applied after the expansion
    # is created.
    raw = await handle_set_fields(
        action,
        fields={"item_ids": "A, B"},
        for_each_staged={
            "1": {"title": "Widget A", "quantity": "2"},
            "2": {"title": "Widget B", "quantity": "1"},
        },
        visitor=SimpleNamespace(),
    )
    payload = json.loads(raw)

    # The parent field should be stored successfully.
    assert payload["ok"] is True

    # for_each expansion should be created with 2 items.
    state = get_for_each_state(session, "item_ids")
    assert state is not None
    assert state["status"] == STATUS_ACTIVE
    assert len(state["items"]) == 2

    # Item A's staged data should have been applied to the session fields.
    # (Notes is optional and not staged, so the item is not yet complete —
    # the model still needs to fill or skip it.)
    assert session.get_value("title") == "Widget A"
    assert session.get_value("quantity") == "2"

    # Item B's staged data should be waiting in _for_each_staged for when
    # iteration reaches item B.
    stage_store = session.context.get("_for_each_staged", {})
    item_b_staged = stage_store.get("B", {})
    assert item_b_staged.get("title") == "Widget B"
    assert item_b_staged.get("quantity") == "1"


@pytest.mark.asyncio
async def test_for_each_staged_partial_saves_while_current_item_incomplete(
    for_each_action,
):
    """Future-item values must be saved immediately even when item 1 is incomplete.

    Mirrors "a laptop, 532 and a phone 231": current item gets title+quantity in
    fields; item 2 gets the same via for_each_staged while notes is still pending.
    """
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    load = _load_fn(spec)
    visitor = SimpleNamespace()

    await handle_set_fields(action, fields={"item_ids": "A, B"}, visitor=visitor)

    raw = await handle_set_fields(
        action,
        fields={"title": "Widget A", "quantity": "2"},
        for_each_staged={"2": {"title": "Widget B", "quantity": "1"}},
        visitor=visitor,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True

    # Item 1 incomplete (optional notes still pending) — must not block item 2 save.
    assert session.get_value("title") == "Widget A"
    assert session.get_value("quantity") == "2"
    assert not session.has_field("notes") and not session.is_skipped("notes")
    state = get_for_each_state(session, "item_ids")
    assert state["status"] == STATUS_ACTIVE
    assert int(state.get("current_index") or 0) == 0

    stage_store = session.context.get("_for_each_staged", {})
    assert stage_store.get("B", {}).get("title") == "Widget B"
    assert stage_store.get("B", {}).get("quantity") == "1"

    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "notes"

    # Finish item 1 → staged item 2 must apply (no re-ask for title/quantity).
    await action._handle_skip_field(field="notes", visitor=visitor)
    assert session.get_value("title") == "Widget B"
    assert session.get_value("quantity") == "1"
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "notes"
    assert (
        "second" in (nxt.get("prompt") or "").lower() or nxt["for_each"]["index"] == 2
    )


@pytest.mark.asyncio
async def test_for_each_staged_description_only_while_current_incomplete(
    for_each_action,
):
    """Partial multi-item dump (titles only) must stage item 2 before item 1 finishes.

    Mirrors "a laptop and a phone": only description/title for each item.
    """
    action, spec = for_each_action
    session = InterviewSession(interview_type=spec.name)
    action._get_session_and_contract = AsyncMock(return_value=(session, spec))
    action._save_session = AsyncMock()
    load = _load_fn(spec)
    visitor = SimpleNamespace()

    await handle_set_fields(action, fields={"item_ids": "A, B"}, visitor=visitor)

    raw = await handle_set_fields(
        action,
        fields={"title": "Widget A"},
        for_each_staged={"2": {"title": "Widget B"}},
        visitor=visitor,
    )
    payload = json.loads(raw)
    assert payload["ok"] is True

    assert session.get_value("title") == "Widget A"
    assert not session.has_field("quantity")
    state = get_for_each_state(session, "item_ids")
    assert int(state.get("current_index") or 0) == 0

    stage_store = session.context.get("_for_each_staged", {})
    assert stage_store.get("B", {}).get("title") == "Widget B"
    assert "quantity" not in stage_store.get("B", {})

    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "quantity"

    await handle_set_fields(action, fields={"quantity": "2"}, visitor=visitor)
    await action._handle_skip_field(field="notes", visitor=visitor)

    assert session.get_value("title") == "Widget B"
    assert not session.has_field("quantity")
    nxt = await build_next_field(session, spec, load)
    assert nxt["key"] == "quantity"
    assert nxt["for_each"]["index"] == 2


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
