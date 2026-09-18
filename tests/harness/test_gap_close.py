"""Gap-close proofs: dump/load, leases, signatures, isolation wrap, retention."""

from __future__ import annotations

from pathlib import Path

import pytest

from jvagent.action.code_execution.executor import ExecRequest
from jvagent.harness.contracts import (
    HarnessContractError,
    IdempotencyClass,
    NativeCaller,
)
from jvagent.harness.isolation import wrap_isolated_command
from jvagent.harness.leases import FileLeaseBackend, lease_backend_for
from jvagent.harness.persist import dump_store, load_store
from jvagent.harness.provider import provider_for
from jvagent.harness.runtime import (
    TRACE_KIND,
    HarnessRuntime,
    HarnessStore,
    SessionBusy,
    SkillIsolationRefused,
    SkillManifest,
)

pytestmark = pytest.mark.harness_conformance


@pytest.fixture
def caller() -> NativeCaller:
    return NativeCaller("ag", "u1", "s1")


def test_dump_store_round_trip(tmp_path: Path, caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore(), worker_id="w1")
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
    rt.finish_invocation(correlation_id=corr, record=rec, result="hello")
    rt.append_event(
        session_id=caller.session_id,
        kind="final",
        message_id="m1",
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
    )
    path = tmp_path / "store.json"
    dump_store(rt.store, path)
    restored = load_store(path)
    other = HarnessRuntime(restored, worker_id="w2")
    journal = other.get_run(corr)
    assert journal is not None
    assert rec.invocation_id in journal.completed_invocation_ids
    assert [e.message_id for e in other.replay_from(caller.session_id)] == ["m1"]


def test_file_lease_backend_contends(tmp_path: Path):
    path = tmp_path / "leases.json"
    a = FileLeaseBackend(path)
    b = FileLeaseBackend(path)
    a.acquire("s1", "w1")
    with pytest.raises(SessionBusy):
        b.acquire("s1", "w2")
    a.release("s1", "w1")
    b.acquire("s1", "w2")
    assert b.get("s1")["worker_id"] == "w2"


def test_redis_and_dynamo_backends_require_clients():
    with pytest.raises(ValueError, match="no silent fallback"):
        lease_backend_for("redis")
    with pytest.raises(ValueError, match="no silent fallback"):
        lease_backend_for("dynamodb")


def test_runtime_file_lease_backend(tmp_path: Path, caller: NativeCaller):
    backend = lease_backend_for("file", path=tmp_path / "leases.json")
    a = HarnessRuntime(HarnessStore(), worker_id="w1", lease_backend=backend)
    b = HarnessRuntime(a.store, worker_id="w2", lease_backend=backend)
    a.acquire_session_lease(caller.session_id)
    with pytest.raises(SessionBusy):
        b.acquire_session_lease(caller.session_id)


def test_skill_signature_and_revoke(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore(), skill_signing_key="k")
    digest = "abc123"
    good = SkillManifest(
        skill_key="k",
        source="app",
        digest=digest,
        declared_tools=(),
        capabilities=(),
        trust_tier="trusted",
        signature=rt.sign_digest(digest),
    )
    rt.register_manifest(good)
    with pytest.raises(HarnessContractError, match="signature"):
        rt.register_manifest(
            SkillManifest(
                skill_key="k",
                source="app",
                digest="other",
                declared_tools=(),
                capabilities=(),
                trust_tier="trusted",
                signature="deadbeef",
            )
        )
    snap = rt.admit_snapshot(caller)
    rt.revoke_manifest(digest)
    with pytest.raises(HarnessContractError, match="revoked"):
        rt.activate_skill(caller, snap.snapshot_id, digest)


@pytest.mark.harness_isolation
def test_wrap_isolated_command_refuses_missing_binary():
    req = ExecRequest(command="echo hi", cwd="/tmp")
    with pytest.raises(SkillIsolationRefused):
        wrap_isolated_command("nsjail", req)
    with pytest.raises(SkillIsolationRefused):
        wrap_isolated_command("docker", req)


@pytest.mark.harness_isolation
def test_wrap_isolated_command_prefixes_when_binary_present(monkeypatch):
    monkeypatch.setattr(
        "jvagent.harness.isolation.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    req = ExecRequest(command="echo hi", cwd="/tmp")
    wrapped = wrap_isolated_command("nsjail", req)
    assert wrapped.command.startswith("nsjail -Mo --cwd /tmp -- /bin/sh -c ")
    assert "echo hi" in wrapped.command


def test_prune_retention_trims_traces(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore(), max_trace_spans=3)
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    for i in range(8):
        rt.record_span(corr, f"tick-{i}")
    rt.prune_retention()
    assert len(rt.traces_for(corr)) == 3


def test_journal_and_event_pagination(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore())
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    for i in range(5):
        rt.append_event(
            session_id=caller.session_id,
            kind="chunk",
            message_id=f"m{i}",
            correlation_id=corr,
            snapshot_id=snap.snapshot_id,
        )
    page = rt.replay_from(caller.session_id, limit=2)
    assert [e.message_id for e in page] == ["m0", "m1"]
    page2 = rt.replay_from(caller.session_id, cursor=page[-1].cursor, limit=2)
    assert [e.message_id for e in page2] == ["m2", "m3"]
    entries = rt.journal_entries(corr, after_seq=0, limit=1)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_host_provider_invokes_registered_runner(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore())
    rt.put_host_tools(caller.session_id, ["host_lookup"])

    async def _lookup(payload: dict) -> dict:
        return {"ok": True, "q": payload.get("q")}

    rt.register_host_runner(caller.session_id, "host_lookup", _lookup)
    snap = rt.admit_snapshot(caller)
    provider = provider_for("native", rt)
    result = await provider.invoke(snap.snapshot_id, "inv-1", "host_lookup", {"q": "x"})
    assert result.ok is True
    assert result.payload["q"] == "x"


def test_persist_writes_trace_kind(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore())
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.record_span(corr, "model_tick", caller=caller.as_tuple())

    class _Ix:
        def __init__(self) -> None:
            self.observability_metrics: list = []

    ix = _Ix()
    rt.persist_to_interaction(ix, corr)
    kinds = {m.get("kind") for m in ix.observability_metrics if isinstance(m, dict)}
    assert TRACE_KIND in kinds


def test_background_phase(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore())
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rt.mark_background(corr)
    assert rt.get_run(corr).plan_phase == "background"


def test_in_memory_redis_adapter_set_nx(caller: NativeCaller):
    class _Redis:
        def __init__(self) -> None:
            self.d: dict = {}

        def set(self, k, v, nx=False, ex=None, xx=False):
            if nx and k in self.d:
                return False
            self.d[k] = v
            return True

        def get(self, k):
            return self.d.get(k)

        def expire(self, k, ttl):
            return k in self.d

        def delete(self, k):
            self.d.pop(k, None)

    backend = lease_backend_for("redis", redis_client=_Redis())
    a = HarnessRuntime(HarnessStore(), worker_id="w1", lease_backend=backend)
    b = HarnessRuntime(a.store, worker_id="w2", lease_backend=backend)
    a.acquire_session_lease(caller.session_id)
    with pytest.raises(SessionBusy):
        b.acquire_session_lease(caller.session_id)


def test_compensate_without_registration_raises(caller: NativeCaller):
    rt = HarnessRuntime(HarnessStore())
    snap = rt.admit_snapshot(caller)
    corr = rt.new_correlation()
    rt.start_turn(corr, caller, snap)
    rec, _ = rt.begin_invocation(
        correlation_id=corr,
        snapshot_id=snap.snapshot_id,
        tool_name="book",
        payload={"n": 1},
        idempotency_class=IdempotencyClass.COMPENSATABLE,
    )
    with pytest.raises(HarnessContractError, match="compensator"):
        rt.compensate(rec.invocation_id)
