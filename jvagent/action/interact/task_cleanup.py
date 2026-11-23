"""Transport teardown that lets the SDK finish its own cancellation lifecycle."""

import asyncio
from typing import Any

import anyio


def request_interaction_cancellation(task: asyncio.Task[Any]) -> bool:
    """Request task cancellation without interrupting already-running cleanup."""
    if task.done():
        return False
    cancelling = getattr(task, "cancelling", None)
    if cancelling is None or not cancelling():
        task.cancel()
    return True


async def cancel_and_drain_interaction(task: asyncio.Task[Any]) -> None:
    """Forward cancellation once and observe completion under request teardown.

    This neither terminalizes SDK records nor replays execution. The owning
    SDK/graph adapter must settle its own receipts before the task completes.
    """
    request_interaction_cancellation(task)
    with anyio.CancelScope(shield=True):
        try:
            await task
        except asyncio.CancelledError:
            pass
