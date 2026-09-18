"""HC-07 / HP-10: traces isolated by NativeCaller."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import NativeCaller
from jvagent.harness.runtime import HarnessRuntime, HarnessStore

pytestmark = pytest.mark.harness_conformance


def test_correlation_explains_one_caller_only():
    rt = HarnessRuntime(HarnessStore(), worker_id="w1")
    a = NativeCaller("ag", "u1", "s1")
    b = NativeCaller("ag", "u2", "s2")
    snap_a = rt.admit_snapshot(a)
    corr_a = rt.new_correlation()
    rt.start_turn(corr_a, a, snap_a)
    rt.record_span(corr_a, "model_tick", caller=a.as_tuple())
    rt.record_span(corr_a, "delivery", caller=a.as_tuple())
    snap_b = rt.admit_snapshot(b)
    corr_b = rt.new_correlation()
    rt.start_turn(corr_b, b, snap_b)
    rt.record_span(corr_b, "model_tick", caller=b.as_tuple())
    doc = rt.replay_document(corr_a)
    assert doc["caller"]["user_id"] == "u1"
    assert doc["correlation_id"] == corr_a
    assert all(s.get("caller") != b.as_tuple() for s in doc["spans"])
