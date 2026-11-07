"""Status must not create or migrate a lead profile as a side effect."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jvspatial.core.context import get_default_context

from jvagent.action.leadgen import engine
from jvagent.action.leadgen.leadgen_action import LeadGenAction
from jvagent.action.leadgen.store import LeadRecord
from jvagent.memory.user import User


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_status_reads_without_persisting_profile_changes(
    existing: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await User.create(memory_id="memory", user_id="caller")
    monkeypatch.setattr(
        engine,
        "get_user_and_interaction",
        AsyncMock(return_value=(user, SimpleNamespace(id="turn"))),
    )
    action = LeadGenAction(
        default_fields={"full_name": {"required": True}, "email": {"required": True}}
    )
    before = None
    if existing:
        record = await LeadRecord.get_or_create_for_user(
            user, required_fields=["full_name"]
        )
        await record.set_yaml({"full_name": "Casey"})
        before = await get_default_context().database.get("node", record.id)
    result = json.loads(await engine.handle_status(action))
    assert result["required_fields"] == ["full_name", "email"]
    assert result["missing_fields"] == (
        ["email"] if existing else ["full_name", "email"]
    )
    if existing:
        assert result["score"] == 50
        assert await get_default_context().database.get("node", record.id) == before
    else:
        assert await LeadRecord.get_for_user(user) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_misdirected_user_edge_cannot_read_another_profile(legacy: bool) -> None:
    user = await User.create(memory_id="memory", user_id="caller")
    other = await User.create(memory_id="memory", user_id="other")
    record = await LeadRecord.create(
        user_node_id="" if legacy else other.id,
        user_id=other.user_id,
        yaml_frontmatter='{"email":"private fixture"}',
    )
    await user.connect(record, direction="out")
    before = await get_default_context().database.get("node", record.id)
    with pytest.raises(ValueError, match="ownership"):
        await LeadRecord.get_for_user(user)
    assert await get_default_context().database.get("node", record.id) == before
