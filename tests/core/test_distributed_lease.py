"""Generic distributed lease used by the bootstrap lock (ADR-0033)."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import jvagent.core.lease_backend as lb
from jvagent.core.distributed_lease import distributed_lease

pytestmark = pytest.mark.asyncio


async def test_inprocess_lease_serializes(monkeypatch):
    # No Redis/Dynamo configured → in-process fallback.
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", raising=False)
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", raising=False)
    order: list = []

    async def worker(name: str) -> None:
        async with distributed_lease("k-serialize"):
            order.append(f"{name}_start")
            await asyncio.sleep(0.05)
            order.append(f"{name}_end")

    await asyncio.gather(worker("a"), worker("b"))

    # One fully finished before the other started.
    assert order.index("a_end") < order.index("b_start") or order.index(
        "b_end"
    ) < order.index("a_start")


async def test_distinct_keys_do_not_block(monkeypatch):
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_REDIS_URL", raising=False)
    monkeypatch.delenv("JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE", raising=False)
    both_in = asyncio.Event()
    count = {"n": 0}

    async def worker(key: str) -> None:
        async with distributed_lease(key):
            count["n"] += 1
            if count["n"] == 2:
                both_in.set()
            await asyncio.wait_for(both_in.wait(), timeout=1.0)

    # Distinct keys must be able to hold their leases simultaneously.
    await asyncio.gather(worker("k-a"), worker("k-b"))
    assert both_in.is_set()


async def test_redis_lease_acquires_renews_releases(monkeypatch):
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
    monkeypatch.setattr(lb, "lease_renew_interval", lambda ttl: 0.02)

    async with distributed_lease("k-redis"):
        await asyncio.sleep(0.1)

    assert counts["acquire"] >= 1
    assert counts["renew"] >= 2
    assert counts["unlock"] == 1
