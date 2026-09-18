"""HP-11 named benches. Not a claim of production p95; bounds the in-process store."""

from __future__ import annotations

import asyncio
import time

import pytest

from jvagent.harness.contracts import NativeCaller
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = [pytest.mark.harness_conformance, pytest.mark.harness_load]


def test_snapshot_creation_budget():
    rt = HarnessRuntime(HarnessStore())
    t0 = time.perf_counter()
    for i in range(200):
        rt.admit_snapshot(NativeCaller("ag", f"u{i}", f"s{i}"))
    assert (time.perf_counter() - t0) * 1000 < 2000


def test_graph_session_and_turnrun_budget():
    rt = HarnessRuntime(HarnessStore())
    t0 = time.perf_counter()
    for i in range(100):
        caller = NativeCaller("ag", f"u{i}", f"s{i}")
        rt.upsert_user("mem", caller.user_id)
        rt.upsert_conversation("mem", caller.session_id)
        snap = rt.admit_snapshot(caller)
        corr = rt.new_correlation()
        rt.start_turn(corr, caller, snap)
        rt.complete_turn(corr)
    assert (time.perf_counter() - t0) * 1000 < 2000


def test_streaming_fan_out_budget():
    rt = HarnessRuntime(HarnessStore())
    caller = NativeCaller("ag", "u", "s")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    t0 = time.perf_counter()
    for i in range(500):
        rt.append_event(
            session_id=caller.session_id,
            kind="chunk",
            message_id=f"m{i}",
            correlation_id=corr,
            snapshot_id=snap.snapshot_id,
        )
    page = rt.replay_from(caller.session_id, limit=50)
    assert len(page) == 50
    assert (time.perf_counter() - t0) * 1000 < 2000


def test_long_session_pruning_budget():
    rt = HarnessRuntime(HarnessStore(), max_trace_spans=50)
    caller = NativeCaller("ag", "u", "s")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    t0 = time.perf_counter()
    for i in range(400):
        rt.record_span(corr, "tick")
        rt.append_event(
            session_id=caller.session_id,
            kind="chunk",
            message_id=f"m{i}",
            correlation_id=corr,
            snapshot_id=snap.snapshot_id,
        )
    rt.prune_retention()
    assert len(rt.traces_for(corr)) <= 50
    assert (time.perf_counter() - t0) * 1000 < 2000


@pytest.mark.asyncio
async def test_parallel_tool_execution_budget():
    store = HarnessStore()

    async def _one(i: int) -> None:
        rt = HarnessRuntime(store, worker_id=f"w{i}")
        caller = NativeCaller("ag", f"u{i}", f"s{i}")
        snap = rt.admit_snapshot(caller)
        corr = rt.new_correlation()
        rt.start_turn(corr, caller, snap)
        rec, _ = rt.begin_invocation(
            correlation_id=corr,
            snapshot_id=snap.snapshot_id,
            tool_name="echo",
            payload={"i": i},
        )
        rt.finish_invocation(correlation_id=corr, record=rec, result="ok")

    t0 = time.perf_counter()
    await asyncio.gather(*[_one(i) for i in range(40)])
    assert (time.perf_counter() - t0) * 1000 < 2000
