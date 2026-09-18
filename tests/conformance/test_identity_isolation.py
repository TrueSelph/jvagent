"""HC-01: native identity isolates callers."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import NativeCaller
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = pytest.mark.harness_conformance


def test_native_caller_identity_is_host_neutral():
    a = NativeCaller("agent-a", "user-1", "sess-1")
    b = NativeCaller("agent-a", "user-2", "sess-1")
    c = NativeCaller("agent-a", "user-1", "sess-2")
    assert a != b
    assert a != c
    assert a.as_tuple()[0] == "agent-a"


def test_concurrent_creates_yield_one_user_and_conversation():
    store = HarnessStore()
    w1 = HarnessRuntime(store, worker_id="w1")
    w2 = HarnessRuntime(store, worker_id="w2")
    u1 = w1.upsert_user("mem-1", "user-1")
    u2 = w2.upsert_user("mem-1", "user-1")
    c1 = w1.upsert_conversation("mem-1", "sess-1")
    c2 = w2.upsert_conversation("mem-1", "sess-1")
    assert u1 == u2
    assert c1 == c2
    assert len(store.identities) == 1
    assert len(store.conversations) == 1
