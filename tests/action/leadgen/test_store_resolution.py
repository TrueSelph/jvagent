"""Regression tests for LeadRecord persistence identity.

The bug: the unique index backing ``LeadRecord.user_node_id`` was not scoped to
the ``LeadProfile`` entity, so the section node (``LeadRecordNode``, which also
stores ``context.user_node_id``) shared one physical unique index with the
anchor. Saving a section via ``append_to_section`` REPLACED the anchor row,
wiping ``yaml_frontmatter`` to ``{}``. A later ``get_or_create_for_user`` then
saw an empty profile and ``leadgen__sync_to_sheet`` bailed with
``profile incomplete`` — which also suppressed the sales email.

The failure is SQLite-specific: ``SQLiteDB.save`` uses ``INSERT OR REPLACE``,
so a second row colliding on a unique index deletes the first. The default test
backend (JsonDB) has no such behaviour, so the behavioural tests bind SQLite
explicitly and close it so the aiosqlite worker thread cannot outlive the test.
"""

from __future__ import annotations

import uuid

import pytest

from jvagent.action.leadgen.store import LeadRecord, LeadRecordNode


def _user_node_id_index(cls) -> dict:
    """The single-field index definition on ``context.user_node_id``, if any."""
    for index in cls.get_indexes():
        if index.get("field") == "context.user_node_id":
            return index
    return {}


def test_lead_record_user_index_is_entity_scoped():
    """The unique key must be partial on ``entity == 'LeadProfile'``.

    Without the entity scope the section node (``LeadProfileNode``) collides on
    the same physical index and REPLACEs the anchor.
    """
    index = _user_node_id_index(LeadRecord)
    assert index, "LeadRecord must index context.user_node_id"
    assert index.get("unique") is True
    partial = index.get("partialFilterExpression") or {}
    assert partial.get("entity") == "LeadProfile"


def test_section_node_does_not_declare_competing_unique_index():
    """The section node must not declare its own unique ``user_node_id`` index.

    A single-field index on the same field would take the same auto-generated
    name as the anchor's and the two entities would fight over one index.
    """
    index = _user_node_id_index(LeadRecordNode)
    assert index.get("unique") is not True


@pytest.mark.asyncio
async def test_append_to_section_does_not_wipe_lead_record():
    """Writing a narrative section must not clobber the anchor's fields."""
    from jvspatial.core.context import (
        GraphContext,
        clear_default_context,
        set_default_context,
    )
    from jvspatial.db.sqlite import SQLiteDB

    from jvagent.memory.user import User

    db = SQLiteDB(db_path=":memory:")
    ctx = GraphContext(database=db)
    set_default_context(ctx)
    try:
        user = await User.create(memory_id="mem-1", user_id=f"u_{uuid.uuid4().hex[:8]}")
        record = await LeadRecord.get_or_create_for_user(
            user, required_fields=["name", "organization", "email", "phone"]
        )
        await record.update_yaml(
            {
                "name": "Tharick",
                "email": "jtharick@example.com",
                "phone": "+5926431530",
                "organization": "Personal",
                "interested_products": "2 steel toe safety boots, size 42",
            }
        )

        await record.append_to_section(
            "conversation_summaries", "Set: name = 'Tharick'"
        )

        fresh = await LeadRecord.get(record.id)
        assert fresh is not None, "anchor row must survive the section write"
        assert fresh.get_yaml().get("email") == "jtharick@example.com"
        assert fresh.get_missing_fields() == []

        # Capture (required_fields) and the custom-tool path (none) still agree.
        sync = await LeadRecord.get_or_create_for_user(user)
        assert sync.id == record.id
        assert sync.get_missing_fields() == []
    finally:
        await db.close()
        clear_default_context()
