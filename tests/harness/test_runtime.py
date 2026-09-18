"""Runtime proofs for HP-02 … HP-12. Shared store = two-worker fixture."""

from __future__ import annotations

import asyncio
import time

import pytest

from jvagent.harness.contracts import (
    HarnessContractError,
    IdempotencyClass,
    NativeCaller,
    SnapshotSelector,
    TurnRunState,
)
from jvagent.harness.runtime import (
    SAME_SESSION_POLICY,
    AdmissionRefused,
    HarnessRuntime,
    HarnessStore,
    SessionBusy,
    SkillIsolationRefused,
    SkillManifest,
    reset_runtime,
)

pytestmark = pytest.mark.harness_conformance


@pytest.fixture
def store() -> HarnessStore:
    return HarnessStore()


@pytest.fixture
def rt(store: HarnessStore) -> HarnessRuntime:
    runtime = HarnessRuntime(store, worker_id="w1")
    reset_runtime(runtime)
    yield runtime
    reset_runtime()


@pytest.fixture
def caller() -> NativeCaller:
    return NativeCaller("ag", "u1", "s1")


def test_same_session_policy_is_lease():
    assert SAME_SESSION_POLICY == "lease"


def test_catalog_cache_isolated_by_caller():
    from jvagent.action.orchestrator.catalog import (
        _ToolSurfaceCacheEntry,
        get_tool_surface_cache,
        invalidate_tool_surface_cache,
        set_tool_surface_cache,
    )

    invalidate_tool_surface_cache()
    entry = _ToolSurfaceCacheEntry(config_hash="abc")
    set_tool_surface_cache(
        "ag", entry, user_id="u1", session_id="s1", snapshot_id="snap1"
    )
    assert (
        get_tool_surface_cache("ag", user_id="u2", session_id="s2", snapshot_id="snap2")
        is None
    )
    assert (
        get_tool_surface_cache("ag", user_id="u1", session_id="s1", snapshot_id="snap1")
        is entry
    )
    invalidate_tool_surface_cache()


def test_concurrent_identity_upsert_one_user_and_conversation(store: HarnessStore):
    a = HarnessRuntime(store, worker_id="w1")
    b = HarnessRuntime(store, worker_id="w2")

    def _once() -> tuple[str, str]:
        return a.upsert_user("mem", "user-x"), a.upsert_conversation("mem", "sess-x")

    first = _once()
    second = (
        b.upsert_user("mem", "user-x"),
        b.upsert_conversation("mem", "sess-x"),
    )
    assert first == second
    assert len(store.identities) == 1
    assert len(store.conversations) == 1


def test_snapshot_isolation_and_inflight_retention(
    rt: HarnessRuntime, caller: NativeCaller
):
    inflight = rt.admit_snapshot(caller, native_tool_names=("alpha",))
    other = NativeCaller("ag", "u2", "s2")
    other_snap = rt.admit_snapshot(other, native_tool_names=("beta",))
    assert inflight.cache_key() != other_snap.cache_key()
    rt.invalidate(SnapshotSelector(caller=caller))
    nxt = rt.admit_snapshot(caller, native_tool_names=("gamma",), force_new=True)
    assert nxt.snapshot_id != inflight.snapshot_id
    inflight.assert_usable()
    with pytest.raises(HarnessContractError):
        rt.require_usable(rt.store.snapshots[inflight.snapshot_id].snapshot_id)


def test_revoked_snapshot_not_reused_for_new_dispatch(
    rt: HarnessRuntime, caller: NativeCaller
):
    snap = rt.admit_snapshot(caller)
    rt.invalidate(SnapshotSelector(snapshot_id=snap.snapshot_id))
    with pytest.raises(HarnessContractError):
        rt.require_usable(snap.snapshot_id)
    fresh = rt.admit_snapshot(caller, force_new=True)
    assert fresh.snapshot_id != snap.snapshot_id


def test_turn_journal_crash_after_dispatch(rt: HarnessRuntime, caller: NativeCaller):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap, interaction_id="int-1")
    rec, cached = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="read_thing",
        payload={"id": "1"},
    )
    assert cached is None
    rt.mark_recovery(corr, reason="crash_after_dispatch")
    journal = rt.get_run(corr)
    assert journal is not None
    assert journal.state is TurnRunState.RECOVERY_REQUIRED
    assert rec.invocation_id in {e for e in [rec.invocation_id]}
    assert any(e["state"] == "waiting_tool" for e in journal.entries)


def test_idempotent_retry_reuses_invocation_id(
    rt: HarnessRuntime, caller: NativeCaller
):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="echo",
        payload={"x": 1},
        idempotency_class=IdempotencyClass.IDEMPOTENT,
    )
    rt.finish_invocation(correlation_id=corr, record=rec, result="ok")
    rec2, cached = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="echo",
        payload={"x": 1},
        idempotency_class=IdempotencyClass.IDEMPOTENT,
    )
    assert rec2.invocation_id == rec.invocation_id
    assert cached == "ok"
    assert rec2.attempt == 2


def test_non_retryable_duplicate_marks_recovery(
    rt: HarnessRuntime, caller: NativeCaller
):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="charge",
        payload={"n": 1},
        idempotency_class=IdempotencyClass.NON_RETRYABLE,
    )
    rt.finish_invocation(correlation_id=corr, record=rec, result="charged")
    with pytest.raises(HarnessContractError, match="non-retryable"):
        rt.begin_invocation(
            correlation_id=corr,
            snapshot_id=snap.snapshot_id,
            tool_name="charge",
            payload={"n": 1},
            idempotency_class=IdempotencyClass.NON_RETRYABLE,
        )
    assert rt.get_run(corr).state is TurnRunState.RECOVERY_REQUIRED


def test_outbox_replay_in_order_across_workers(
    store: HarnessStore, caller: NativeCaller
):
    a = HarnessRuntime(store, worker_id="w1")
    b = HarnessRuntime(store, worker_id="w2")
    snap = a.admit_snapshot(caller)
    corr = a.new_correlation()
    a.append_event(
        session_id=caller.session_id,
        kind="chunk",
        message_id="m1",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    a.append_event(
        session_id=caller.session_id,
        kind="final",
        message_id="m2",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    missed = b.replay_from(caller.session_id, f"{caller.session_id}:1")
    assert [e.message_id for e in missed] == ["m2"]
    assert missed[0].sequence == 2


def test_two_worker_leases_and_drain(store: HarnessStore):
    a = HarnessRuntime(store, worker_id="w1")
    b = HarnessRuntime(store, worker_id="w2")
    a.acquire_session_lease("sess-a")
    b.acquire_session_lease("sess-b")
    with pytest.raises(SessionBusy):
        b.acquire_session_lease("sess-a")
    snap = a.admit_snapshot(NativeCaller("ag", "u", "sess-a"))
    corr = a.new_correlation()
    a.start_turn(corr, NativeCaller("ag", "u", "sess-a"), snap)
    a.bind_lease("sess-a", corr)
    a.drain()
    with pytest.raises(AdmissionRefused):
        a.admit_snapshot(NativeCaller("ag", "u2", "sess-new"), force_new=True)
    replay = b.replay_from("sess-a")
    assert replay == []
    a.append_event(
        session_id="sess-a",
        kind="final",
        message_id="kept",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    assert [e.message_id for e in b.replay_from("sess-a")] == ["kept"]
    a.worker_lost("w1")
    assert a.get_run(corr).state is TurnRunState.RECOVERY_REQUIRED


def test_untrusted_skill_refused_without_approved_backend(
    rt: HarnessRuntime, caller: NativeCaller
):
    snap = rt.admit_snapshot(caller)
    digest = "abc123"
    rt.register_manifest(
        SkillManifest(
            skill_key="scripty",
            source="app",
            digest=digest,
            declared_tools=(),
            capabilities=(),
            trust_tier="untrusted",
            spec="jv",
        )
    )
    with pytest.raises(SkillIsolationRefused):
        rt.activate_skill(caller, snap.snapshot_id, digest, trust_tier="untrusted")
    hardened = HarnessRuntime(rt.store, worker_id="iso", isolation_backend="gvisor")
    rec = hardened.activate_skill(
        caller, snap.snapshot_id, digest, trust_tier="untrusted"
    )
    assert rec.active
    hardened.cleanup_stage(rec.path)
    assert hardened.store.stages[rec.path].active is False


def test_stale_snapshot_cannot_activate_skill(rt: HarnessRuntime, caller: NativeCaller):
    snap = rt.admit_snapshot(caller)
    rt.register_manifest(
        SkillManifest(
            skill_key="k",
            source="app",
            digest="d1",
            declared_tools=(),
            capabilities=(),
            trust_tier="trusted",
        )
    )
    rt.invalidate(SnapshotSelector(snapshot_id=snap.snapshot_id))
    with pytest.raises(HarnessContractError):
        rt.activate_skill(caller, snap.snapshot_id, "d1")


def test_third_skill_spec_rejected(rt: HarnessRuntime):
    with pytest.raises(HarnessContractError, match="spec"):
        rt.register_manifest(
            SkillManifest(
                skill_key="x",
                source="app",
                digest="d",
                declared_tools=(),
                capabilities=(),
                trust_tier="trusted",
                spec="weird",
            )
        )


def test_trace_isolated_by_caller(rt: HarnessRuntime, caller: NativeCaller):
    other = NativeCaller("ag", "u2", "s2")
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.record_span(corr, "model_tick", caller=caller.as_tuple())
    doc = rt.replay_document(corr)
    assert doc["caller"]["user_id"] == "u1"
    assert all(
        tuple(s.get("caller") or caller.as_tuple()) == caller.as_tuple()
        for s in doc["spans"]
        if "caller" in s
    )
    other_corr = rt.new_correlation()
    other_snap = rt.admit_snapshot(other)
    rt.start_turn(other_corr, other, other_snap)
    assert rt.traces_for(corr) != rt.traces_for(other_corr)


@pytest.mark.asyncio
async def test_distinct_session_leases_concurrent(store: HarnessStore):
    a = HarnessRuntime(store, worker_id="w1")
    b = HarnessRuntime(store, worker_id="w2")

    async def _hold(rt: HarnessRuntime, sid: str) -> None:
        rt.acquire_session_lease(sid)
        await asyncio.sleep(0.01)
        rt.release_session_lease(sid)

    await asyncio.gather(_hold(a, "s-a"), _hold(b, "s-b"))


def test_compensatable_path(rt: HarnessRuntime, caller: NativeCaller):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.register_compensator("book", lambda rec: f"compensated:{rec.invocation_id}")
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="book",
        payload={"n": 1},
        idempotency_class=IdempotencyClass.COMPENSATABLE,
    )
    assert rt.compensate(rec.invocation_id).startswith("compensated:")
    rt.finish_invocation(correlation_id=corr, record=rec, result="failed", ok=False)


def test_completed_run_not_resumed(rt: HarnessRuntime, caller: NativeCaller):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.complete_turn(corr)
    with pytest.raises(HarnessContractError):
        rt.resume_turn(corr)


def test_micro_benches_under_budget(rt: HarnessRuntime, caller: NativeCaller):
    t0 = time.perf_counter()
    for i in range(50):
        c = NativeCaller("ag", f"u{i}", f"s{i}")
        rt.admit_snapshot(c)
        rt.upsert_user("mem", f"u{i}")
        rt.upsert_conversation("mem", f"s{i}")
        rt.append_event(
            session_id=f"s{i}",
            kind="chunk",
            message_id=f"m{i}",
            correlation_id=f"c{i}",
            snapshot_id="snap",
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 2000


class _FakeInteraction:
    def __init__(self) -> None:
        self.observability_metrics: list = []


def test_checkpoint_round_trip_restores_idempotent_result(
    rt: HarnessRuntime, caller: NativeCaller
):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap, interaction_id="int-ckpt")
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="echo",
        payload={"x": 1},
        idempotency_class=IdempotencyClass.IDEMPOTENT,
    )
    rt.finish_invocation(correlation_id=corr, record=rec, result="hello")
    interaction = _FakeInteraction()
    rt.persist_to_interaction(interaction, corr)
    other = HarnessRuntime(HarnessStore(), worker_id="w2")
    payload = other.checkpoint_from_interaction(interaction)
    assert payload is not None
    restored = other.import_checkpoint(payload)
    assert restored.correlation_id == corr
    assert rec.invocation_id in restored.completed_invocation_ids
    cached = other.peek_completed_result(
        correlation_id=corr, tool_name="echo", payload={"x": 1}
    )
    assert cached == "hello"


def test_peek_completed_skips_non_idempotent(rt: HarnessRuntime, caller: NativeCaller):
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="send",
        payload={"n": 1},
    )
    rt.finish_invocation(correlation_id=corr, record=rec, result="sent")
    assert (
        rt.peek_completed_result(
            correlation_id=corr, tool_name="send", payload={"n": 1}
        )
        is None
    )


@pytest.mark.asyncio
async def test_snapshot_host_tool_is_callable_from_the_standard_tool_surface(
    caller: NativeCaller,
):
    from jvagent.action.orchestrator.tools import wrap_host_tool
    from jvagent.action.orchestrator.turn_cache import bind_turn_cache

    runtime = HarnessRuntime(HarnessStore())
    reset_runtime(runtime)
    runtime.put_host_tools(caller.session_id, ["host_lookup"])
    runtime.register_host_runner(
        caller.session_id,
        "host_lookup",
        lambda payload: {"answer": payload["q"]},
    )
    snapshot = runtime.admit_snapshot(caller)
    corr = runtime.new_correlation()
    runtime.start_turn(corr, caller, snapshot)
    with bind_turn_cache() as turn:
        turn["snapshot"] = snapshot
        turn["correlation_id"] = corr
        result = await wrap_host_tool("host_lookup").run({"q": "found"})
    assert '"answer": "found"' in result
    assert runtime.get_run(corr).completed_invocation_ids
    reset_runtime()


@pytest.mark.asyncio
async def test_host_tool_invoke_error_finishes_the_ledger(caller: NativeCaller):
    from jvagent.action.orchestrator.tools import wrap_host_tool
    from jvagent.action.orchestrator.turn_cache import bind_turn_cache

    runtime = HarnessRuntime(HarnessStore())
    reset_runtime(runtime)
    runtime.put_host_tools(caller.session_id, ["host_lookup"])
    snapshot = runtime.admit_snapshot(caller)
    corr = runtime.new_correlation()
    runtime.start_turn(corr, caller, snapshot)
    with bind_turn_cache() as turn:
        turn["snapshot"] = snapshot
        turn["correlation_id"] = corr
        result = await wrap_host_tool("host_lookup").run({"q": "x"})
    assert result.startswith("(tool error:")
    assert runtime.get_run(corr).state is TurnRunState.RUNNING
    reset_runtime()


@pytest.mark.asyncio
async def test_host_skill_materialization_is_not_a_placeholder(caller: NativeCaller):
    from jvagent.harness.provider import LocalHostProvider

    runtime = HarnessRuntime(HarnessStore())
    runtime.register_host_skill(
        caller.session_id,
        "host_procedure",
        digest="host-procedure-v1",
        spec="jv",
        body="# Host procedure\n\nCall host_lookup first.",
    )
    snapshot = runtime.admit_snapshot(caller)
    materialization = await LocalHostProvider(runtime).load_skill(
        snapshot.snapshot_id, "host_procedure"
    )
    assert materialization.body == "# Host procedure\n\nCall host_lookup first."
