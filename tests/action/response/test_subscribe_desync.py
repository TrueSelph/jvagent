"""Regression: ResponseBus.subscribe must survive a resumed session whose
subscriber maps desynced.

The idle-eviction pass evicted sessions whose ``_subscribers[sid]`` was an empty
list (falsy) and popped ``_subscriber_ids[sid]`` — but left the empty
``_subscribers[sid]`` behind. A later ``subscribe`` for that session then saw it
in ``_subscribers``, skipped initialization, and raised
``KeyError`` on ``_subscriber_ids[session_id]`` — surfacing to streaming clients
as a mid-stream "Something went wrong" error on conversation resume.
"""

from jvagent.action.response.response_bus import ResponseBus


def _noop(_msg):
    return None


async def test_subscribe_survives_desynced_maps():
    bus = ResponseBus()
    # Simulate the post-eviction desync directly.
    bus._subscribers["sess_x"] = []
    bus._subscriber_ids.pop("sess_x", None)

    # Must not raise KeyError.
    await bus.subscribe("sess_x", _noop)

    assert _noop in bus._subscribers["sess_x"]
    assert id(_noop) in bus._subscriber_ids["sess_x"]


def test_idle_eviction_drops_both_subscriber_maps():
    bus = ResponseBus()
    sid = "sess_y"
    # An idle session with an empty (falsy) subscribers list.
    bus._subscribers[sid] = []
    bus._subscriber_ids[sid] = set()
    bus._session_queues[sid] = object()
    bus._session_queue_activity[sid] = 0.0  # ancient → past the idle window
    bus._last_cleanup_time = 0.0  # force the lazy cleanup to run

    bus._maybe_cleanup()

    # Both maps must be cleared in lockstep — no desync left behind.
    assert sid not in bus._subscriber_ids
    assert sid not in bus._subscribers
    assert sid not in bus._session_queues
