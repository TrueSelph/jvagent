"""InteractWalker access enforcement (unified with background path)."""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.access_control.access_control_action import AccessControlAction
from jvagent.action.interact.interact_walker import InteractWalker


def _make_interact_mock(
    *,
    class_name: str = "PersonaAction",
    label: str = "p",
    weight: int = 0,
    run_in_background: bool = False,
    deny_directive: Optional[str] = None,
):
    m = MagicMock()
    m.enabled = True
    m.label = label
    m.weight = weight
    m.run_in_background = run_in_background
    m.deny_access_directive = deny_directive
    m.get_class_name = lambda: class_name
    return m


@pytest.mark.asyncio
async def test_enforce_interact_action_access_denies_and_reports():
    ac = AccessControlAction(
        permissions={
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
                "PersonaAction": {
                    "deny": [],
                    "allow": [{"group": "admins"}],
                },
            },
        },
        user_groups={"default": {"admins": ["user_abc"]}},
        default_deny=True,
        enforce=True,
        enabled=True,
    )
    walker = InteractWalker(
        agent_id="agent_x",
        utterance="hi",
        channel="default",
        user_id="user_denied",
    )
    mock_agent = MagicMock()
    mock_agent.id = "agent_x"
    mock_agent.get_access_control_action = AsyncMock(return_value=ac)
    walker._agent = mock_agent
    walker.interaction = MagicMock()
    walker.interaction.directives = []
    walker.interaction.save = AsyncMock()
    walker.report = AsyncMock()

    action = _make_interact_mock()
    allowed = await walker.enforce_interact_action_access(action, stage="walker")
    assert allowed is False
    walker.report.assert_awaited()


@pytest.mark.asyncio
async def test_enforce_interact_action_access_allows_when_no_access_control_node():
    walker = InteractWalker(
        agent_id="agent_x",
        utterance="hi",
        channel="default",
        user_id="anyone",
    )
    mock_agent = MagicMock()
    mock_agent.get_access_control_action = AsyncMock(return_value=None)
    walker._agent = mock_agent

    action = _make_interact_mock()
    assert await walker.enforce_interact_action_access(action, stage="walker") is True
