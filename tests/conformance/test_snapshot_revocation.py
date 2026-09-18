"""HC-02: snapshot cannot leak or reuse after expiry/revocation."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import (
    HarnessContractError,
    NativeCaller,
    SnapshotSelector,
    ToolSurfaceSnapshot,
)
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = pytest.mark.harness_conformance


def test_two_callers_have_distinct_snapshot_cache_keys():
    a = ToolSurfaceSnapshot(
        snapshot_id="snap-a",
        caller=NativeCaller("ag", "u1", "s1"),
        native_tool_names=(),
        native_skill_keys=(),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2026-09-17T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        revoked=False,
    )
    b = ToolSurfaceSnapshot(
        snapshot_id="snap-b",
        caller=NativeCaller("ag", "u2", "s2"),
        native_tool_names=(),
        native_skill_keys=(),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2026-09-17T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        revoked=False,
    )
    a.assert_usable()
    assert a.cache_key() != b.cache_key()


def test_revoked_snapshot_fixture_is_unusable():
    snap = ToolSurfaceSnapshot(
        snapshot_id="snap-revoked",
        caller=NativeCaller("ag", "u1", "s1"),
        native_tool_names=(),
        native_skill_keys=(),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2026-09-17T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        revoked=True,
    )
    with pytest.raises(HarnessContractError):
        snap.assert_usable()


def test_dynamic_tool_change_does_not_contaminate_inflight_snapshot():
    store = HarnessStore()
    rt = HarnessRuntime(store, worker_id="w1")
    caller = NativeCaller("ag", "u1", "s1")
    inflight = rt.admit_snapshot(caller, native_tool_names=("alpha",))
    other = NativeCaller("ag", "u2", "s2")
    other_snap = rt.admit_snapshot(other, native_tool_names=("beta",))
    assert "alpha" in inflight.native_tool_names
    assert "beta" in other_snap.native_tool_names
    rt.put_host_tools(caller.session_id, ["gamma"])
    later = rt.admit_snapshot(caller, force_new=True)
    assert later.snapshot_id != inflight.snapshot_id
    assert "gamma" in later.host_tool_names
    assert "gamma" not in inflight.host_tool_names
    rt.invalidate(SnapshotSelector(snapshot_id=later.snapshot_id))
    with pytest.raises(HarnessContractError):
        rt.require_usable(later.snapshot_id)
    inflight.assert_usable()
