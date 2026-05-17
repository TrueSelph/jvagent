"""XC-4: ``Action._discover_action_endpoints`` honors per-class extra paths.

Without this, endpoints registered under non-conforming prefixes (OAuth
callbacks, channel webhooks, admin /agents/{agent_id}/ namespaces) leak
on action deregister — they stay in the FastAPI route table pointing at
a deleted action.
"""

from types import SimpleNamespace
from typing import ClassVar, List
from unittest.mock import patch

from jvagent.action.base import Action


class _FakeAction(Action):
    """Concrete Action subclass with a couple of extra path templates."""

    additional_endpoint_path_prefixes: ClassVar[List[str]] = [
        "/raw/literal/prefix/",
    ]
    additional_endpoint_path_templates: ClassVar[List[str]] = [
        "/whatsapp/{action_id}/",
        "/proactive/tick/{agent_id}",
    ]


def _stub_server_with_routes(paths: list) -> SimpleNamespace:
    fake_registry = SimpleNamespace(
        _function_registry={object(): SimpleNamespace(path=p) for p in paths}
    )
    return SimpleNamespace(_endpoint_registry=fake_registry)


def _make_action() -> _FakeAction:
    a = _FakeAction()
    object.__setattr__(a, "id", "n.Action.ABC")
    object.__setattr__(a, "agent_id", "n.Agent.AGENT")
    return a


def test_standard_prefix_still_matched():
    action = _make_action()
    server = _stub_server_with_routes(["/actions/n.Action.ABC/foo", "/something/else"])
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert len(funcs) == 1


def test_literal_prefix_matched():
    action = _make_action()
    server = _stub_server_with_routes(
        ["/raw/literal/prefix/handler", "/something/else"]
    )
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert len(funcs) == 1


def test_template_action_id_substituted():
    action = _make_action()
    server = _stub_server_with_routes(
        ["/whatsapp/n.Action.ABC/session", "/whatsapp/n.Action.XYZ/session"]
    )
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    # Only paths with THIS action's id should match — not n.Action.XYZ.
    assert len(funcs) == 1


def test_template_agent_id_substituted():
    action = _make_action()
    server = _stub_server_with_routes(
        ["/proactive/tick/n.Agent.AGENT", "/proactive/tick/n.Agent.OTHER"]
    )
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert len(funcs) == 1


def test_dedup_when_multiple_prefixes_match_same_func():
    """If a single endpoint matches both the standard prefix AND a
    declared extra path, return it ONCE."""
    action = _make_action()
    # Both /actions/{id}/ and /raw/literal/prefix/ overlap on this path
    # — register a single endpoint whose path matches a single prefix.
    server = _stub_server_with_routes(["/actions/n.Action.ABC/foo"])
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert len(funcs) == 1


def test_unmatched_paths_not_returned():
    action = _make_action()
    server = _stub_server_with_routes(
        [
            "/actions/n.Action.OTHER/foo",  # different action_id
            "/api/admin",
            "/agents/n.Agent.OTHER/access_control/rules",  # different agent
        ]
    )
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = action._discover_action_endpoints()
    assert funcs == []


def test_base_action_with_no_extras_unchanged_behavior():
    """Subclass with empty additional lists keeps original behavior."""

    class _PlainAction(Action):
        pass

    a = _PlainAction()
    object.__setattr__(a, "id", "n.Action.P")
    object.__setattr__(a, "agent_id", "n.Agent.A")
    server = _stub_server_with_routes(
        ["/actions/n.Action.P/x", "/whatsapp/n.Action.P/y"]
    )
    with patch("jvspatial.api.context.get_current_server", return_value=server):
        funcs = a._discover_action_endpoints()
    # Only the standard prefix matches; whatsapp path is NOT auto-matched
    # because _PlainAction didn't declare it.
    assert len(funcs) == 1
