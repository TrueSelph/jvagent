"""Conversation turn-lock lease renewal (AUDIT-memory HIGH, C7).

The Redis/Dynamo lease has a fixed TTL (45s) with no renewal; a multi-step
orchestrator turn exceeds it, the lease lapses mid-turn, and a second worker
acquires it and runs concurrently. The lock now heartbeats to renew the lease
while held."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import jvagent.memory.distributed_conversation_lock as dcl

pytestmark = pytest.mark.asyncio


async def test_lease_renew_interval_is_below_ttl():
    assert dcl._lease_renew_interval(45) == pytest.approx(15.0)
    assert dcl._lease_renew_interval(5) == pytest.approx(1.667, abs=0.01)
    assert dcl._lease_renew_interval(1) == 1.0  # floor


async def test_heartbeat_renews_until_cancelled():
    calls = []

    async def renew():
        calls.append(1)

    task = asyncio.create_task(
        dcl._run_lease_heartbeat(renew, interval=0.02, conversation_id="c")
    )
    await asyncio.sleep(0.11)  # ~5 intervals
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 3  # renewed repeatedly


async def test_heartbeat_survives_a_failed_renew():
    calls = []

    async def renew():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    task = asyncio.create_task(
        dcl._run_lease_heartbeat(renew, interval=0.02, conversation_id="c")
    )
    await asyncio.sleep(0.11)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # A transient failure did not kill the loop — it kept renewing.
    assert len(calls) >= 3


async def test_redis_lock_renews_lease_while_held(monkeypatch):
    counts = {"acquire": 0, "renew": 0, "unlock": 0}

    class _FakeRedis:
        async def set(self, name, value, nx, ex):
            counts["acquire"] += 1
            return True

        async def eval(self, script, numkeys, *args):
            if "expire" in script:
                counts["renew"] += 1
            elif "del" in script:
                counts["unlock"] += 1
            return 1

        async def close(self):
            pass

    fake_asyncio = types.ModuleType("redis.asyncio")
    fake_asyncio.from_url = lambda url, decode_responses=True: _FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio)

    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", "redis://fake:6379")
    # Shrink the heartbeat so the test doesn't wait real TTL seconds.
    monkeypatch.setattr(dcl, "_lease_renew_interval", lambda ttl: 0.02)

    async with dcl.conversation_mutation_lock("conv-renew"):
        await asyncio.sleep(0.1)  # ~5 heartbeats

    assert counts["acquire"] >= 1
    assert counts["renew"] >= 2  # lease was renewed mid-hold
    assert counts["unlock"] == 1  # released exactly once


async def test_redis_lock_stops_renewing_after_release(monkeypatch):
    counts = {"renew": 0}

    class _FakeRedis:
        async def set(self, name, value, nx, ex):
            return True

        async def eval(self, script, numkeys, *args):
            if "expire" in script:
                counts["renew"] += 1
            return 1

        async def close(self):
            pass

    fake_asyncio = types.ModuleType("redis.asyncio")
    fake_asyncio.from_url = lambda url, decode_responses=True: _FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio)
    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr(dcl, "_lease_renew_interval", lambda ttl: 0.02)

    async with dcl.conversation_mutation_lock("conv-stop"):
        await asyncio.sleep(0.05)
    renews_at_release = counts["renew"]

    # No further renewals after the context exits.
    await asyncio.sleep(0.1)
    assert counts["renew"] == renews_at_release
