"""Module-unload must not unload a sibling whose name is a prefix (AUDIT-actions M23).

Deregistering action ``foo`` matched ``jvagent.actions.ns.foo`` with a bare
startswith, which also matches the still-registered sibling ``foo_bar``
(``jvagent.actions.ns.foo_bar``) — unloading its modules and breaking it until
re-import. The match must be on a dotted-component boundary."""

from __future__ import annotations

import sys
import types

import pytest

from jvagent.action.base import Action

pytestmark = pytest.mark.asyncio


def _make_action(name: str, loaded_modules):
    a = Action()
    object.__setattr__(a, "id", f"n.Action.{name}")
    a.metadata = {
        "namespace": "acme",
        "name": name,
        "loaded_modules": loaded_modules,
    }
    return a


async def test_sibling_prefix_module_not_unloaded(monkeypatch):
    own = "jvagent.actions.acme.demo.foo.foo"
    own_sub = "jvagent.actions.acme.demo.foo.foo.helpers"
    sibling = "jvagent.actions.acme.demo.foo_bar.foo_bar"

    for m in (own, own_sub, sibling):
        monkeypatch.setitem(sys.modules, m, types.ModuleType(m))

    # metadata pattern is jvagent.actions.<namespace>.<name>; use name="demo.foo"
    # so the pattern is the parent of own/own_sub but NOT of the sibling.
    action = _make_action("demo.foo", [own, own_sub, sibling])

    count = await action._unload_action_modules()

    assert own not in sys.modules  # own module unloaded
    assert own_sub not in sys.modules  # own submodule unloaded
    assert sibling in sys.modules  # sibling PRESERVED (pre-fix: unloaded)
    assert count == 2


async def test_exact_module_unloaded(monkeypatch):
    exact = "jvagent.actions.acme.solo.solo"
    monkeypatch.setitem(sys.modules, exact, types.ModuleType(exact))
    action = _make_action("solo.solo", [exact])
    # pattern jvagent.actions.acme.solo.solo == exact
    count = await action._unload_action_modules()
    assert exact not in sys.modules
    assert count == 1
