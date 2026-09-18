"""Minimal host-neutral fixture. Not a product host."""

from __future__ import annotations

from jvagent.harness.contracts import NativeCaller
from jvagent.harness.provider import provider_for
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

FAKE_HOST_ID = "fake-host"


def fake_host_runtime() -> HarnessRuntime:
    store = HarnessStore()
    rt = HarnessRuntime(store, worker_id="fake-host")
    rt.put_host_tools("sess-conformance", ["host_lookup"])
    rt.put_host_skills("sess-conformance", ["host_skill"])
    return rt


def fake_caller() -> NativeCaller:
    return NativeCaller("agent-conformance", "user-conformance", "sess-conformance")


__all__ = ["FAKE_HOST_ID", "fake_caller", "fake_host_runtime", "provider_for"]
