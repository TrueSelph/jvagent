"""Tests for ADR-0033 upsert-by-identity lookups."""

from __future__ import annotations

import pytest

from jvagent.action.base import Action
from jvagent.core.agent import Agent
from jvagent.core.upsert import (
    find_action_context_records,
    upsert_lookup_by_action_identity,
    upsert_lookup_by_singleton_archetype,
)

pytestmark = pytest.mark.asyncio

ARCHETYPE = "AccessControlAction"


async def _persist(agent_id: str, ns: str, label: str, *, archetype: str) -> None:
    action = await Action.create(
        namespace=ns,
        label=label,
        metadata={"class": archetype, "config": {"singleton": True}},
    )
    object.__setattr__(action, "agent_id", agent_id)
    await action.save()


async def test_upsert_lookup_by_action_identity(test_db):
    agent = await Agent.create(
        name="upsert_agent", namespace="test", alias="U", description="d"
    )
    await _persist(agent.id, "jvagent", "access_control_action", archetype=ARCHETYPE)

    lookup = await upsert_lookup_by_action_identity(
        agent.id, "jvagent", "access_control_action"
    )

    assert len(lookup.records) == 1
    assert lookup.keeper_id is not None


async def test_upsert_lookup_by_singleton_archetype_finds_all_dupes(test_db):
    agent = await Agent.create(
        name="dup_agent", namespace="test", alias="D", description="d"
    )
    for _ in range(3):
        await _persist(
            agent.id, "jvagent", "access_control_action", archetype=ARCHETYPE
        )

    lookup = await upsert_lookup_by_singleton_archetype(agent.id, ARCHETYPE)

    assert len(lookup.records) == 3
    records = await find_action_context_records(agent.id, archetype=ARCHETYPE)
    assert len(records) == 3
