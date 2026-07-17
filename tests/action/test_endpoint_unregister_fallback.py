"""Endpoint-unregister fallback must not crash on mutate-during-iteration
(AUDIT-actions M16).

The fallback sweep iterated registry._function_registry.items() and called
unregister_function() inside the loop; unregister deletes from that same dict, so
the live-view iteration raised "dictionary changed size during iteration" on the
first hit — caught and swallowed, leaking every remaining route. It must snapshot
first and unregister them all."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jvagent.action.base import Action

pytestmark = pytest.mark.asyncio


class _Registry:
    def __init__(self, func_paths):
        # {func_obj: path}
        self._function_registry = {
            f: SimpleNamespace(path=p) for f, p in func_paths.items()
        }

    def unregister_function(self, func) -> bool:
        if func in self._function_registry:
            del self._function_registry[func]  # mutates the dict mid-sweep
            return True
        return False


def _make_action(action_id="n.Action.XYZ"):
    a = Action()
    object.__setattr__(a, "id", action_id)
    object.__setattr__(a, "agent_id", "n.Agent.A")
    return a


async def test_fallback_unregisters_all_missed_routes():
    action = _make_action()
    prefix = f"/actions/{action.id}/"
    # fx is "tracked" (returned by discovery); fa/fb/fc are prefix-matching
    # routes only the fallback sweep catches. Before the fix, unregistering fa
    # while iterating the live dict raised and left fb/fc leaked.
    fx, fa, fb, fc, other = (object() for _ in range(5))
    registry = _Registry(
        {
            fx: prefix + "tracked",
            fa: prefix + "a",
            fb: prefix + "b",
            fc: prefix + "c",
            other: "/other/x",
        }
    )
    server = SimpleNamespace(_endpoint_registry=registry)
    action._discover_action_endpoints = lambda: [fx]  # type: ignore[method-assign]

    with patch("jvspatial.api.context.get_current_server", return_value=server):
        count = await action._unregister_endpoints()

    assert count == 4  # fx (tracked) + fa/fb/fc (fallback); pre-fix leaked fb/fc
    remaining = [i.path for i in registry._function_registry.values()]
    assert remaining == ["/other/x"]


async def test_unrelated_routes_untouched():
    action = _make_action()
    fx, keep, other = (object() for _ in range(3))
    registry = _Registry(
        {
            fx: f"/actions/{action.id}/tracked",
            keep: "/actions/n.Action.OTHER/keep",
            other: "/other/y",
        }
    )
    server = SimpleNamespace(_endpoint_registry=registry)
    action._discover_action_endpoints = lambda: [fx]  # type: ignore[method-assign]

    with patch("jvspatial.api.context.get_current_server", return_value=server):
        count = await action._unregister_endpoints()

    assert count == 1  # only the tracked route for THIS action
    remaining = {i.path for i in registry._function_registry.values()}
    assert remaining == {"/actions/n.Action.OTHER/keep", "/other/y"}


async def test_no_server_is_noop():
    action = _make_action()
    with patch("jvspatial.api.context.get_current_server", return_value=None):
        assert await action._unregister_endpoints() == 0
