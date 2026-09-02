"""Graph repair cursor serialization and per-node edge sync (C3/C4)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.core import graph_repair_job
from jvagent.core.repair_phases.types import (
    PH_ORPHANS_INTERACTION,
    PH_ORPHANS_REATTACH,
    PH_SYNC_APPLY,
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
async def test_sync_apply_queries_expected_edges_per_node():
    node_a = "n.Node.a"
    node_b = "n.Node.b"
    edge_a1 = "e.edge.a1"
    edge_b1 = "e.edge.b1"
    run_id = "run-sync"

    page_nodes = [
        {"id": node_a, "edges": []},
        {"id": node_b, "edges": []},
    ]
    prefix_calls = []

    async def _scratch_page(_db, _rid, kind, after_key, limit):
        if kind == "valid_edge":
            if after_key is None:
                return [{"key": edge_a1}]
            return [{"key": edge_b1}]
        return []

    async def _scratch_page_key_prefix(_db, _rid, kind, key_prefix, after_key, limit):
        prefix_calls.append(key_prefix)
        if key_prefix == f"{node_a}|":
            return [{"key": f"{node_a}|{edge_a1}"}]
        if key_prefix == f"{node_b}|":
            return [{"key": f"{node_b}|{edge_b1}"}]
        return []

    node_objs = {
        node_a: SimpleNamespace(id=node_a, edge_ids=[]),
        node_b: SimpleNamespace(id=node_b, edge_ids=[]),
    }
    for n in node_objs.values():
        n.save = AsyncMock()

    context = SimpleNamespace(database=object())

    async def _deserialize(_cls, data):
        return node_objs[data["id"]]

    context._deserialize_entity = AsyncMock(side_effect=_deserialize)

    state = {
        "dry_run": False,
        "phase": PH_SYNC_APPLY,
        "cursor": {"last_node_id": "", "run_id": run_id},
        "result": {"node_edge_ids_synced": 0},
    }
    limits = RepairLimits(batch_size=10, max_seconds=5)

    with (
        patch.object(
            graph_repair_job, "_find_nodes_page", new=AsyncMock(return_value=page_nodes)
        ),
        patch(
            "jvagent.core.repair_scratch.scratch_page",
            new=AsyncMock(side_effect=_scratch_page),
        ),
        patch(
            "jvagent.core.repair_scratch.scratch_page_key_prefix",
            new=AsyncMock(side_effect=_scratch_page_key_prefix),
        ),
    ):
        await graph_repair_job._tick_sync_apply(context, state, limits)

    assert f"{node_a}|" in prefix_calls
    assert f"{node_b}|" in prefix_calls
    assert node_objs[node_a].edge_ids == [edge_a1]
    assert node_objs[node_b].edge_ids == [edge_b1]
    assert state["result"]["node_edge_ids_synced"] == 2
