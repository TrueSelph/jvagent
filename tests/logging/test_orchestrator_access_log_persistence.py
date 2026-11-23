"""The Orchestrator's security-denial events reach the graph log database."""

import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from jvspatial.db.jsondb import JsonDB
from jvspatial.logging import DBLogHandler

from jvagent.action.orchestrator.access import is_tool_allowed


@pytest.mark.asyncio
async def test_access_policy_failure_is_persisted_without_exception_details(test_db):
    log_db = JsonDB(base_path=str(test_db / "logs"))
    handler = DBLogHandler(
        database_name="logs",
        database=log_db,
        log_levels={logging.ERROR},
    )
    logger = logging.getLogger("jvagent.action.orchestrator.access")
    logger.addHandler(handler)
    agent = Mock()
    agent.id = "agent-security-smoke"
    agent.get_access_control_action = AsyncMock(
        side_effect=RuntimeError("secret connection detail")
    )

    before = asyncio.all_tasks()
    try:
        allowed = await is_tool_allowed(
            agent,
            label="tool:delegate:search",
            user_id="caller-security-smoke",
            channel="web",
        )
        assert not allowed

        # DBLogHandler persists asynchronously on the current event loop.
        writes = asyncio.all_tasks() - before
        if writes:
            await asyncio.gather(*writes)

        entries = await log_db.find("object", {})
    finally:
        logger.removeHandler(handler)
        handler.close()

    records = [
        entry
        for entry in entries
        if entry.get("context", {}).get("event_code")
        == "orchestrator_access_policy_failure"
    ]
    assert len(records) == 1, repr(entries)
    data = records[0]["context"]["log_data"]
    assert data["event"] == "orchestrator_access_policy_failure"
    assert data["action_label"] == "tool:delegate:search"
    assert data["channel"] == "web"
    assert data["actor_present"] is True
    assert data["stage"] == "orchestrator"
    assert data["reason"] == "policy_resolution_or_evaluation_error"
    assert data["exception_type"] == "RuntimeError"
    assert "secret connection detail" not in str(data)
