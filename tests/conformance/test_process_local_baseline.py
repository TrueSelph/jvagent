"""HP-01 characterization plus HP-11 micro-benches of harness hot paths."""

from __future__ import annotations

import inspect
import time

import pytest

from jvagent.action.model import resilience
from jvagent.action.orchestrator import catalog, skill_providers, skills, turn_cache
from jvagent.action.response import response_bus
from jvagent.harness.contracts import NativeCaller
from jvagent.harness.runtime import HarnessRuntime, HarnessStore
from jvagent.memory import lock_manager

pytestmark = pytest.mark.harness_conformance


def test_response_bus_registry_is_process_local():
    assert isinstance(response_bus._agent_bus_registry, dict)
    assert inspect.iscoroutinefunction(response_bus.get_agent_response_bus)


def test_tool_surface_cache_is_keyed_by_snapshot_and_caller():
    params = list(inspect.signature(catalog.get_tool_surface_cache).parameters)
    assert params == ["agent_id", "user_id", "session_id", "snapshot_id"]
    assert isinstance(catalog._TOOL_SURFACE_CACHE, dict)


def test_skill_discovery_cache_is_process_local():
    assert isinstance(skills._SKILL_DISCOVERY_CACHE, dict)


def test_host_skill_providers_are_process_global():
    assert isinstance(skill_providers._providers, list)


def test_model_breaker_is_process_local():
    assert resilience.MODEL_BREAKER is not None
    assert isinstance(resilience.MODEL_BREAKER._states, dict)
    original = resilience.MODEL_BREAKER._states
    shared: dict = {}
    resilience.MODEL_BREAKER.bind_shared_backend(shared)
    assert resilience.MODEL_BREAKER._states is shared
    resilience.MODEL_BREAKER.bind_shared_backend(original)


def test_turn_cache_is_contextvar_not_module_dict():
    assert turn_cache._turn_cache is not None
    assert hasattr(turn_cache._turn_cache, "get")
    with turn_cache.bind_turn_cache() as bound:
        assert bound is turn_cache.get_turn_cache()
    assert turn_cache.get_turn_cache() is None


def test_memory_locks_are_in_process():
    mgr = lock_manager.get_conversation_lock_manager()
    assert isinstance(mgr._locks, dict)


def test_streaming_dedup_exists_as_replay_regression():
    from tests.action.response.test_streaming_dedup import (
        test_backlog_message_not_redelivered_from_live_queue,
    )

    assert callable(test_backlog_message_not_redelivered_from_live_queue)


def _rt() -> HarnessRuntime:
    return HarnessRuntime(HarnessStore(), worker_id="bench")


def test_benchmark_short_chat():
    rt = _rt()
    t0 = time.perf_counter()
    rt.admit_snapshot(NativeCaller("a", "u", "s"), native_tool_names=("reply",))
    assert (time.perf_counter() - t0) < 0.5


def test_benchmark_tool_rich_chat():
    rt = _rt()
    names = tuple(f"tool_{i}" for i in range(40))
    t0 = time.perf_counter()
    rt.admit_snapshot(NativeCaller("a", "u", "s"), native_tool_names=names)
    assert (time.perf_counter() - t0) < 0.5


def test_benchmark_streaming():
    rt = _rt()
    t0 = time.perf_counter()
    for i in range(100):
        rt.append_event(
            session_id="s",
            kind="chunk" if i < 99 else "final",
            message_id=f"m{i}",
            correlation_id="c",
            snapshot_id="snap",
        )
    frames = rt.replay_from("s", "s:50")
    assert len(frames) == 50
    assert (time.perf_counter() - t0) < 0.5


def test_benchmark_long_session():
    rt = _rt()
    t0 = time.perf_counter()
    for i in range(200):
        rt.append_event(
            session_id="long",
            kind="chunk",
            message_id=f"m{i}",
            correlation_id="c",
            snapshot_id="snap",
        )
    page = rt.replay_from("long", "long:100", limit=25)
    assert len(page) == 25
    assert (time.perf_counter() - t0) < 0.5


def test_benchmark_many_user():
    rt = _rt()
    t0 = time.perf_counter()
    for i in range(80):
        caller = NativeCaller("a", f"u{i}", f"s{i}")
        rt.upsert_user("mem", caller.user_id)
        rt.upsert_conversation("mem", caller.session_id)
        rt.admit_snapshot(caller)
    assert (time.perf_counter() - t0) < 1.0
