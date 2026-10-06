"""Access policy resolution must distinguish absence from failure."""

from unittest.mock import AsyncMock, Mock

import pytest

from jvagent.action.orchestrator.access import is_tool_allowed


@pytest.mark.asyncio
async def test_absent_access_control_remains_an_explicit_open_policy():
    agent = Mock()
    agent.get_access_control_action = AsyncMock(return_value=None)

    assert await is_tool_allowed(
        agent, label="tool:delegate:search", user_id="user", channel="web"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("policy storage unavailable"), AttributeError("policy_applies")],
)
async def test_access_policy_resolution_failures_deny(failure):
    agent = Mock()
    if isinstance(failure, RuntimeError):
        agent.get_access_control_action = AsyncMock(side_effect=failure)
    else:
        policy = Mock()
        del policy.policy_applies
        agent.get_access_control_action = AsyncMock(return_value=policy)

    assert not await is_tool_allowed(
        agent, label="tool:delegate:search", user_id="user", channel="web"
    )


@pytest.mark.asyncio
async def test_policy_resolution_failure_emits_structured_redacted_security_event(
    caplog,
):
    agent = Mock()
    agent.id = "agent-test"
    agent.get_access_control_action = AsyncMock(
        side_effect=RuntimeError("database password must not appear in logs")
    )

    with caplog.at_level("ERROR", logger="jvagent.action.orchestrator.access"):
        allowed = await is_tool_allowed(
            agent,
            label="tool:delegate:search",
            user_id="user-internal-id",
            channel="web",
        )

    assert not allowed
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "orchestrator_access_policy_failure"
    )
    assert record.event == "orchestrator_access_policy_failure"
    assert record.action_label == "tool:delegate:search"
    assert record.channel == "web"
    assert record.actor_present is True
    assert record.exception_type == "RuntimeError"
    assert "database password" not in caplog.text


@pytest.mark.asyncio
async def test_access_policy_denials_and_policy_evaluation_errors_deny():
    policy = Mock()
    policy.policy_applies.return_value = True
    policy.has_action_access = AsyncMock(return_value=False)
    agent = Mock()
    agent.get_access_control_action = AsyncMock(return_value=policy)
    assert not await is_tool_allowed(
        agent, label="tool:delegate:search", user_id="user", channel="web"
    )

    policy.has_action_access.side_effect = RuntimeError("policy lookup failed")
    assert not await is_tool_allowed(
        agent, label="tool:delegate:search", user_id="user", channel="web"
    )
