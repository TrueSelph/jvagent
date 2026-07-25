"""The long-lived subscribe stream needs keepalives and a bounded replay.

Without a heartbeat, proxies (nginx ``proxy_read_timeout``, ALB, Cloudflare)
drop a stream that stays silent for 60-100s — which a parked messenger tab does
constantly. And because streaming subscribers never drain the session queue,
an unbounded backlog replay re-sends the whole history on every reconnect.
"""

import asyncio

import pytest

from jvagent.action.response.streaming import stream_messages


class _Msg:
    def __init__(self, mid: str, content: str = "hi"):
        self.id = mid
        self.interaction_id = None
        self.content = content

    def to_dict(self):
        return {"id": self.id, "content": self.content}


class _FakeBus:
    """Minimal ResponseBus surface used by stream_messages."""

    def __init__(self, backlog=None):
        self._backlog = backlog or []
        self.subscribed = False

    async def subscribe(self, session_id, callback, receive_chunks=False):
        self.subscribed = True

    async def unsubscribe(self, session_id, callback):
        self.subscribed = False

    async def get_messages(self, session_id):
        return list(self._backlog)


async def _take(gen, n, timeout=2.0):
    """Pull n chunks off the generator, failing rather than hanging."""
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(gen.__anext__(), timeout=timeout))
    return out


@pytest.mark.asyncio
async def test_emits_keepalive_when_idle():
    bus = _FakeBus()
    gen = stream_messages("s1", bus, keepalive_seconds=0.05)
    try:
        chunks = await _take(gen, 2)
        # An idle stream produces SSE comments, not data frames.
        assert all(c == ": keepalive\n\n" for c in chunks)
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_no_keepalive_without_the_option():
    """Turn-scoped streams keep their original silent behaviour."""
    bus = _FakeBus(backlog=[_Msg("m1")])
    gen = stream_messages("s1", bus)
    try:
        first = await _take(gen, 1)
        assert "m1" in first[0]
        # Nothing further should arrive — no keepalive on this path.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(gen.__anext__(), timeout=0.3)
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_backlog_replay_is_bounded():
    bus = _FakeBus(backlog=[_Msg(f"m{i}") for i in range(10)])
    gen = stream_messages("s1", bus, keepalive_seconds=0.05, max_replay=3)
    try:
        chunks = await _take(gen, 3)
        # Only the most recent 3 are replayed.
        assert "m7" in chunks[0]
        assert "m8" in chunks[1]
        assert "m9" in chunks[2]
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_full_backlog_replayed_when_unbounded():
    bus = _FakeBus(backlog=[_Msg(f"m{i}") for i in range(4)])
    gen = stream_messages("s1", bus)
    try:
        chunks = await _take(gen, 4)
        assert "m0" in chunks[0]
        assert "m3" in chunks[3]
    finally:
        await gen.aclose()
