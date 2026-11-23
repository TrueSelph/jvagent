"""Interaction bootstrap (phase 1) for InteractWalker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.access_control.access_control_action import AccessControlAction
from jvagent.action.interact.interact_walker import InteractWalker


@pytest.mark.asyncio
async def test_initialize_interaction_no_memory():
    walker = InteractWalker(
        agent_id="agent_1", utterance="hi", channel="default", user_id="u1"
    )
    agent = MagicMock()
    agent.id = "agent_1"
    agent.get_memory = AsyncMock(return_value=None)
    result = await walker.initialize_interaction(agent)
    assert result.ok is False
    assert result.code == "no_memory"
    assert walker.interaction is None


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_initialize_interaction_access_denied_at_entry(
    monkeypatch: pytest.MonkeyPatch, native: bool
) -> None:
    from jvagent.action.native_harness import NativeHarnessAction

    consume = AsyncMock()
    monkeypatch.setattr(
        "jvagent.core.host_context.consume_host_system_context", consume
    )
    ac = AccessControlAction(
        permissions={
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
                "interact": {"deny": [], "allow": [{"group": "admins"}]},
            },
        },
        user_groups={"default": {"admins": ["admin_1"]}},
        default_deny=True,
        enforce=True,
        enabled=True,
    )
    walker = InteractWalker(
        agent_id="agent_1",
        utterance="hi",
        channel="default",
        user_id="not_admin",
    )
    conv = MagicMock()
    conv.context = {}
    memory = MagicMock()
    memory.get_session = AsyncMock(
        return_value=(
            MagicMock(),
            conv,
            "not_admin",
            "sess_1",
            False,
        )
    )
    agent = MagicMock()
    agent.id = "agent_1"
    agent.get_memory = AsyncMock(return_value=memory)
    agent.get_response_bus = AsyncMock(return_value=MagicMock())
    agent.get_access_control_action = AsyncMock(return_value=ac)
    agent.get_actions = AsyncMock(
        return_value=[NativeHarnessAction()] if native else []
    )

    result = await walker.initialize_interaction(agent)
    assert result.ok is False
    assert result.code == "access_denied"
    assert walker.interaction is None
    consume.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_interaction_session_value_error():
    walker = InteractWalker(
        agent_id="agent_1",
        utterance="hi",
        channel="default",
        session_id="missing_sess",
    )
    memory = MagicMock()
    memory.get_session = AsyncMock(
        side_effect=ValueError("Session 'missing_sess' not found")
    )
    agent = MagicMock()
    agent.id = "agent_1"
    agent.get_memory = AsyncMock(return_value=memory)
    agent.get_response_bus = AsyncMock(return_value=MagicMock())

    result = await walker.initialize_interaction(agent)
    assert result.ok is False
    assert result.code == "session_resolution_error"


@pytest.mark.asyncio
async def test_initialize_interaction_idempotent_when_interaction_exists():
    walker = InteractWalker(agent_id="a", utterance="x", channel="default")
    existing = MagicMock()
    existing.id = "int_existing"
    walker.interaction = existing
    agent = MagicMock()
    result = await walker.initialize_interaction(agent)
    assert result.ok is True
    assert result.code == "ok"
    assert result.detail == "already_initialized"
