"""Stream cancellation edge cases for embed.interact."""

from __future__ import annotations

import asyncio

import pytest

from jvagent.embed.interact import (
    _clear_interact_task,
    _register_interact_task,
    cancel_interact,
)


@pytest.mark.asyncio
async def test_cancel_interact_cancels_registered_task() -> None:
    async def slow_walk() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(slow_walk())
    await _register_interact_task("sess-cancel-test", task)
    await asyncio.sleep(0)

    assert cancel_interact(session_id="sess-cancel-test") is True

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_register_interact_task_cancels_prior_stream_for_same_key() -> None:
    first_done = asyncio.Event()

    async def first() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            first_done.set()

    async def second() -> None:
        await asyncio.sleep(0)

    t1 = asyncio.create_task(first())
    await _register_interact_task("thread-1", t1)
    t2 = asyncio.create_task(second())
    await _register_interact_task("thread-1", t2)

    with pytest.raises(asyncio.CancelledError):
        await t1

    await t2
    await _clear_interact_task("thread-1", t2)
