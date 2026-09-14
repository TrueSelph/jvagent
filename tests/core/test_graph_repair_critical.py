"""Graph repair cursor serialization and removed sync-phase resume."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core import graph_repair_job
from jvagent.core.repair_phases.types import (
    PH_ORPHANS_INTERACTION,
    PH_ORPHANS_LIST_NODES,
    PH_ORPHANS_REATTACH,
    PH_SYNC_APPLY,
    PH_SYNC_PREPARE,
    RepairLimits,
)


@pytest.mark.asyncio
async def test_orphans_reattach_cursor_is_json_serializable():
    ctx = {
        "memories": [],
        "memory_by_id": {},
        "memory_by_agent_id": {},
        "agents": [],
        "agents_without_memory": [],
        "agents_without_actions": [],
    }
    run_id = "run-serializable"
    graph_repair_job._reattach_ctx_by_run[run_id] = ctx

    state = {
        "dry_run": True,
        "phase": PH_ORPHANS_REATTACH,
        "cursor": {
            "orphan_ids": [],
            "orphan_index": 0,
            "run_id": run_id,
        },
        "result": {"orphaned_nodes_reattached": 0},
    }
    limits = RepairLimits(batch_size=10, max_seconds=1)

    await graph_repair_job._tick_orphans_reattach(SimpleNamespace(), state, limits)

    json.dumps(state["cursor"], sort_keys=True)
    assert "reattach_ctx" not in state["cursor"]
    graph_repair_job._reattach_ctx_by_run.pop(run_id, None)


@pytest.mark.asyncio
async def test_reattach_ctx_released_on_force_advance_after_stall():
    run_id = "run-stall-release"
    graph_repair_job._reattach_ctx_by_run[run_id] = {"memories": []}

    state = {
        "dry_run": True,
        "phase": graph_repair_job.PH_ORPHANS_REATTACH,
        "cursor": {"orphan_ids": ["n.Node.x"], "orphan_index": 0, "run_id": run_id},
        "result": graph_repair_job._new_result_counters(),
        "stall_count": 1,
        "run_id": run_id,
    }
    limits = RepairLimits(batch_size=1, max_seconds=0)

    with (
        patch.object(
            graph_repair_job,
            "_tick_orphans_reattach",
            new=AsyncMock(return_value=True),
        ),
        patch.object(graph_repair_job, "_repair_checkpoint", new=AsyncMock()),
    ):
        await graph_repair_job.run_repair_session(state, limits)

    assert run_id not in graph_repair_job._reattach_ctx_by_run
    assert state["phase"] == PH_ORPHANS_INTERACTION


@pytest.mark.asyncio
async def test_reattach_ctx_released_when_repair_state_restarts():
    run_id = "run-restart-release"
    graph_repair_job._reattach_ctx_by_run[run_id] = {"memories": []}

    payload = {
        "v": 999,
        "phase": PH_ORPHANS_REATTACH,
        "cursor": {"run_id": run_id},
        "result": graph_repair_job._new_result_counters(),
        "dry_run": True,
        "run_id": run_id,
    }
    graph_repair_job.state_from_dict(payload, dry_run=True, recent_minutes=None)

    assert run_id not in graph_repair_job._reattach_ctx_by_run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_phase", [PH_SYNC_PREPARE, PH_SYNC_APPLY, "sync_prepare", "sync_apply"]
)
async def test_legacy_sync_phase_advances_to_orphans(legacy_phase):
    """In-flight repairs stuck on removed sync phases skip to orphans."""
    run_id = "run-skip-sync"
    state = {
        "dry_run": True,
        "phase": legacy_phase,
        "cursor": {"last_edge_id": "e.old", "run_id": run_id},
        "result": graph_repair_job._new_result_counters(),
        "run_id": run_id,
        "stall_count": 0,
    }
    limits = RepairLimits(batch_size=10, max_seconds=1)

    with (
        patch.object(
            graph_repair_job,
            "_tick_orphans_list_nodes",
            new=AsyncMock(return_value=True),
        ) as orphans_tick,
        patch.object(graph_repair_job, "_repair_checkpoint", new=AsyncMock()),
    ):
        await graph_repair_job.run_repair_session(state, limits)

    orphans_tick.assert_awaited_once()
    # Mocked tick does not advance; phase must have left the removed sync step.
    assert state["phase"] == PH_ORPHANS_LIST_NODES


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_phase", [PH_SYNC_PREPARE, PH_SYNC_APPLY])
async def test_state_from_dict_skips_legacy_sync_phase(legacy_phase):
    payload = {
        "v": graph_repair_job.STATE_VERSION,
        "phase": legacy_phase,
        "cursor": {"last_edge_id": "e.old", "run_id": "run-load"},
        "result": graph_repair_job._new_result_counters(),
        "dry_run": False,
        "run_id": "run-load",
        "stall_count": 0,
    }
    state = graph_repair_job.state_from_dict(
        payload, dry_run=False, recent_minutes=None
    )
    assert state["phase"] == PH_ORPHANS_LIST_NODES
    assert state["cursor"].get("run_id") == "run-load"
