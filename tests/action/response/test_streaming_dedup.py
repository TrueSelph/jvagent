"""SSE stream_messages must not deliver a message twice (AUDIT-actions M19).

Subscribing before replaying the backlog means a message can appear on both the
replay and the live queue; it must be deduped by message_id."""

from __future__ import annotations

import pytest

from jvagent.action.response.streaming import stream_messages

pytestmark = pytest.mark.asyncio


class _Msg:
    def __init__(self, mid, iid="i1"):
        self.message_id = mid
        self.interaction_id = iid

    def to_dict(self):
        return {"message_id": self.message_id}


class _Bus:
    def __init__(self, existing):
        self._existing = existing
        self._cb = None

    async def subscribe(self, session_id, cb, receive_chunks=False):
        self._cb = cb

    async def get_messages(self, session_id):
        return list(self._existing)

    async def unsubscribe(self, session_id, cb):
        pass


async def test_backlog_message_not_redelivered_from_live_queue():
    m1, m2 = _Msg("m1"), _Msg("m2")
    bus = _Bus([m1])
    gen = stream_messages("s1", bus)

    out = []
    # First chunk: the backlog replay of m1 (subscribe has now run).
    out.append(await gen.__anext__())

    # m1 also arrives on the live queue (the subscribe-before-replay window),
    # plus a genuinely new m2.
    await bus._cb(m1)
    await bus._cb(m2)

    # Next non-timeout chunk must be m2 — m1 is deduped, not re-sent.
    out.append(await gen.__anext__())
    await gen.aclose()

    joined = "".join(out)
    assert joined.count('"m1"') == 1
    assert joined.count('"m2"') == 1


async def test_distinct_messages_all_delivered():
    bus = _Bus([])
    gen = stream_messages("s1", bus)

    # Prime the subscription (first __anext__ subscribes, then blocks on queue).
    import asyncio

    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)  # let it subscribe
    await bus._cb(_Msg("a"))
    first = await task
    await bus._cb(_Msg("b"))
    second = await gen.__anext__()
    await gen.aclose()

    assert '"a"' in first
    assert '"b"' in second
