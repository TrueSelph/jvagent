"""Concurrency test harness for the ADR-0033 substrate work.

Runs many coroutines against the shared per-test graph context and returns their
results. Because jvspatial awaits at every DB hop, un-serialized check-then-create
paths interleave under ``asyncio.gather`` and produce the duplicate nodes the
substrate must prevent — so this reproduces the in-process race deterministically
without threads."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, List


async def run_concurrent(factory: Callable[[int], Awaitable[Any]], n: int) -> List[Any]:
    """Run ``factory(i)`` for i in range(n) concurrently; return results in order.

    ``factory`` must return a fresh awaitable per call. Exceptions propagate as
    results (``return_exceptions=True``) so a caller can assert on them.
    """
    return await asyncio.gather(*(factory(i) for i in range(n)), return_exceptions=True)
