"""Opt-in qualification for the real cross-process Redis conversation lease.

Run with JVAGENT_TEST_REDIS_URL pointing at a disposable shared Redis service.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import queue
import signal
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


def _observe_lock_owner_after_resume(
    redis_url: str,
    conversation_id: str,
    events: Any,
) -> None:
    os.environ["JVAGENT_CONVERSATION_LOCK_REDIS_URL"] = redis_url
    os.environ["JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"] = "5"

    from jvagent.memory.distributed_conversation_lock import (
        conversation_mutation_lock,
    )

    async def run() -> None:
        try:
            async with conversation_mutation_lock(conversation_id):
                events.put(("first", "entered", time.monotonic()))
                await asyncio.sleep(60)
                events.put(("first", "exited", time.monotonic()))
        except asyncio.CancelledError:
            events.put(("first", "cancelled", time.monotonic()))

    asyncio.run(run())


def _briefly_hold_conversation_lock(
    redis_url: str,
    conversation_id: str,
    events: Any,
) -> None:
    os.environ["JVAGENT_CONVERSATION_LOCK_REDIS_URL"] = redis_url
    os.environ["JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"] = "5"

    from jvagent.memory.distributed_conversation_lock import (
        conversation_mutation_lock,
    )

    async def run() -> None:
        async with conversation_mutation_lock(conversation_id):
            events.put(("second", "entered", time.monotonic()))
            await asyncio.sleep(0.2)
            events.put(("second", "exited", time.monotonic()))

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


@pytest.mark.skipif(
    not os.environ.get("JVAGENT_TEST_REDIS_URL"),
    reason="set JVAGENT_TEST_REDIS_URL to run the real Redis multiprocess test",
)
@pytest.mark.skipif(
    os.name != "posix", reason="process suspension qualification requires POSIX signals"
)
def test_resumed_async_owner_is_cancelled_after_redis_lease_expiry() -> None:
    """A resumed cooperative owner must not continue after losing its lease."""
    pytest.importorskip("redis.asyncio")
    redis_url = os.environ["JVAGENT_TEST_REDIS_URL"]
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    conversation_id = f"redis-paused-{os.urandom(12).hex()}"
    first = context.Process(
        target=_observe_lock_owner_after_resume,
        args=(redis_url, conversation_id, events),
    )
    second = context.Process(
        target=_briefly_hold_conversation_lock,
        args=(redis_url, conversation_id, events),
    )
    observed: list[tuple[str, str, float]] = []
    first.start()
    stopped = False
    try:
        observed.append(events.get(timeout=10))
        os.kill(first.pid, signal.SIGSTOP)
        stopped = True
        time.sleep(6)
        second.start()
        observed.append(events.get(timeout=10))
        observed.append(events.get(timeout=10))
        os.kill(first.pid, signal.SIGCONT)
        stopped = False
        observed.append(events.get(timeout=10))
        first.join(timeout=10)
        second.join(timeout=10)
        assert first.exitcode == 0
        assert second.exitcode == 0
        assert [(worker, event) for worker, event, _ in observed] == [
            ("first", "entered"),
            ("second", "entered"),
            ("second", "exited"),
            ("first", "cancelled"),
        ]
    finally:
        if stopped and first.is_alive():
            os.kill(first.pid, signal.SIGCONT)
        if first.is_alive():
            first.terminate()
            first.join(timeout=5)
        if second.is_alive():
            second.terminate()
            second.join(timeout=5)
        events.close()
        events.join_thread()
