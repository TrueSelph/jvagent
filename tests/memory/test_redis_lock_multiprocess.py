"""Opt-in qualification for the real cross-process Redis conversation lease.

Run with JVAGENT_TEST_REDIS_URL pointing at a disposable shared Redis service.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import queue
import time
from typing import Any

import pytest


def _hold_conversation_lock(
    redis_url: str,
    conversation_id: str,
    hold_seconds: float,
    entered: Any,
) -> None:
    os.environ["JVAGENT_CONVERSATION_LOCK_REDIS_URL"] = redis_url
    os.environ["JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"] = "5"

    from jvagent.memory.distributed_conversation_lock import (
        conversation_mutation_lock,
    )

    async def run() -> None:
        async with conversation_mutation_lock(conversation_id):
            entered.put(time.monotonic())
            await asyncio.sleep(hold_seconds)

    asyncio.run(run())


@pytest.mark.skipif(
    not os.environ.get("JVAGENT_TEST_REDIS_URL"),
    reason="set JVAGENT_TEST_REDIS_URL to run the real Redis multiprocess test",
)
def test_redis_conversation_lease_serializes_workers_and_renews_past_ttl() -> None:
    pytest.importorskip("redis.asyncio")
    redis_url = os.environ["JVAGENT_TEST_REDIS_URL"]
    context = multiprocessing.get_context("spawn")
    entered = context.Queue()
    conversation_id = f"redis-multiprocess-{os.urandom(12).hex()}"
    first = context.Process(
        target=_hold_conversation_lock,
        args=(redis_url, conversation_id, 6.5, entered),
    )
    second = context.Process(
        target=_hold_conversation_lock,
        args=(redis_url, conversation_id, 0, entered),
    )

    first.start()
    try:
        first_entered_at = entered.get(timeout=10)
        second.start()
        with pytest.raises(queue.Empty):
            entered.get(timeout=5.5)

        second_entered_at = entered.get(timeout=10)
        assert second_entered_at - first_entered_at >= 6.0
        first.join(timeout=10)
        second.join(timeout=10)
        assert first.exitcode == 0
        assert second.exitcode == 0
    finally:
        if first.is_alive():
            first.terminate()
            first.join(timeout=5)
        if second.is_alive():
            second.terminate()
            second.join(timeout=5)
        entered.close()
        entered.join_thread()
