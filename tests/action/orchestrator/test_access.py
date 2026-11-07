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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_allowed,named_allowed", [(True, True), (True, False), (False, True)]
)
@pytest.mark.parametrize("explicit", [True, False])
async def test_native_named_tool_permission_preserves_both_gates(
    action_allowed: bool, named_allowed: bool, explicit: bool
) -> None:
    from types import SimpleNamespace

    policy = SimpleNamespace(
        policy_applies=lambda: True,
        tool_permission_names=lambda channel: {"lookup"},
        has_tool_access=AsyncMock(return_value=named_allowed),
        has_action_access=AsyncMock(return_value=action_allowed),
    )
    agent = SimpleNamespace(get_access_control_action=AsyncMock(return_value=policy))
    assert await is_tool_allowed(
        agent,
        label="SearchAction",
        tool_name="lookup",
        requires_tool_permission=explicit,
        user_id="caller",
        channel="web",
    ) is (action_allowed and named_allowed)
    policy.has_tool_access.assert_awaited_once_with("caller", "lookup", "web")


@pytest.mark.asyncio
async def test_native_explicit_named_permission_without_policy_denies() -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(get_access_control_action=AsyncMock(return_value=None))
    assert not await is_tool_allowed(
        agent,
        label="SearchAction",
        tool_name="lookup",
        requires_tool_permission=True,
        user_id="caller",
        channel="web",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["resolve", "names", "check"])
async def test_named_tool_policy_errors_never_grant_access(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace

    from jvagent.tooling.access import is_named_tool_allowed, named_tool_rule_applies

    policy = SimpleNamespace(
        policy_applies=lambda: True,
        tool_permission_names=Mock(return_value={"lookup"}),
        has_tool_access=AsyncMock(return_value=True),
        has_action_access=AsyncMock(return_value=True),
    )
    agent = SimpleNamespace(get_access_control_action=AsyncMock(return_value=policy))
    error = RuntimeError("private policy credential")
    if failure == "resolve":
        agent.get_access_control_action.side_effect = error
    elif failure == "names":
        policy.tool_permission_names.side_effect = error
    else:
        policy.has_tool_access.side_effect = error
    assert not await is_tool_allowed(
        agent, label="SearchAction", tool_name="lookup", user_id="caller", channel="web"
    )
    assert not await is_named_tool_allowed(
        agent, tool_name="lookup", user_id="caller", channel="web"
    )
    assert await named_tool_rule_applies(agent, tool_name="lookup", channel="web")
    assert "private policy credential" not in caplog.text
