"""Conversation turn-lock lease renewal (AUDIT-memory HIGH, C7).

The Redis/Dynamo lease has a fixed TTL (45s) with no renewal; a multi-step
orchestrator turn exceeds it, the lease lapses mid-turn, and a second worker
acquires it and runs concurrently. The lock now heartbeats to renew the lease
while held."""

from __future__ import annotations

import asyncio
import builtins
import sys
import types
from typing import Awaitable, Callable

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


async def _run_owner_with_heartbeat(
    renew: Callable[[], Awaitable[bool | None]],
    *,
    ttl: float,
    interval: float = 0.02,
) -> None:
    owner = asyncio.current_task()
    assert owner is not None
    heartbeat = asyncio.create_task(
        dcl._run_lease_heartbeat(
            renew,
            interval=interval,
            conversation_id="c",
            ttl=ttl,
            owner_task=owner,
        )
    )
    try:
        await asyncio.Event().wait()
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


async def test_heartbeat_cancels_owner_when_backend_proves_lease_lost():
    async def renew():
        return False

    owner = asyncio.create_task(_run_owner_with_heartbeat(renew, ttl=1.0))
    with pytest.raises(asyncio.CancelledError):
        await owner


async def test_heartbeat_cancels_owner_before_unrenewed_lease_expires():
    async def renew():
        raise ConnectionError("temporary backend outage")

    started = asyncio.get_running_loop().time()
    # Keep a scheduling margin before the lease deadline; a 20ms margin made
    # this timing assertion flaky when the full suite briefly starved the loop.
    owner = asyncio.create_task(
        _run_owner_with_heartbeat(renew, ttl=0.16, interval=0.05)
    )
    with pytest.raises(asyncio.CancelledError):
        await owner

    assert asyncio.get_running_loop().time() - started < 0.16


async def test_heartbeat_bounds_a_stalled_renewal_by_lease_deadline():
    async def renew():
        await asyncio.Event().wait()

    started = asyncio.get_running_loop().time()
    owner = asyncio.create_task(
        _run_owner_with_heartbeat(renew, ttl=0.16, interval=0.05)
    )
    with pytest.raises(asyncio.CancelledError):
        await owner

    assert asyncio.get_running_loop().time() - started < 0.16


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


async def test_redis_lock_cancels_turn_after_token_loss(monkeypatch):
    class _FakeRedis:
        async def set(self, name, value, nx, ex):
            return True

        async def eval(self, script, numkeys, *args):
            return 0

        async def close(self):
            pass

    fake_asyncio = types.ModuleType("redis.asyncio")
    fake_asyncio.from_url = lambda url, decode_responses=True: _FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio)
    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", "redis://fake:6379")
    monkeypatch.setattr(dcl, "_lease_renew_interval", lambda ttl: 0.02)

    async def hold_turn():
        async with dcl.conversation_mutation_lock("conv-lost"):
            await asyncio.Event().wait()

    turn = asyncio.create_task(hold_turn())
    with pytest.raises(asyncio.CancelledError):
        await turn


async def test_configured_redis_lock_fails_closed_without_client(monkeypatch):
    original_import = builtins.__import__

    def _without_redis(name, *args, **kwargs):
        if name == "redis.asyncio":
            raise ImportError("redis client unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _without_redis)
    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", "redis://fake:6379")
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", raising=False)

    with pytest.raises(RuntimeError, match="redis>=5 is unavailable"):
        async with dcl.conversation_mutation_lock("conv-no-redis-client"):
            pytest.fail("must not silently downgrade to a process-local lock")


async def test_configured_dynamodb_lock_fails_closed_without_client(monkeypatch):
    original_import = builtins.__import__

    def _without_boto3(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("boto3 unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _without_boto3)
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", raising=False)
    monkeypatch.setenv("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", "conversation-locks")

    with pytest.raises(RuntimeError, match="boto3 is unavailable"):
        async with dcl.conversation_mutation_lock("conv-no-boto3"):
            pytest.fail("must not silently downgrade to a process-local lock")
