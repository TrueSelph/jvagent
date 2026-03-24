"""Tests for AccessControlAction has_action_access and programmatic API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.access_control.access_control_action import AccessControlAction

_ACTION_MODULE = "jvagent.action.access_control.access_control_action"


@pytest.fixture
def access_control_action():
    """Create AccessControlAction with mocked save."""
    action = AccessControlAction(
        permissions={
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
                "PersonaAction": {
                    "deny": [],
                    "allow": [{"group": "admins"}],
                },
            },
            "whatsapp": {
                "any": {
                    "deny": [],
                    "allow": [{"user": "5926431530", "enabled": True}],
                },
            },
        },
        user_groups={
            "admins": ["user_abc", "user_def"],
        },
        exceptions=["ConverseInteractAction"],
        default_deny=True,  # Deny unless explicitly allowed
        action_aliases={"persona": "PersonaAction"},
    )
    action.enabled = True
    action.enforce = True
    action.allow_anonymous = False
    return action


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_allow_all_default(mock_save, access_control_action):
    """User in default channel with 'any' allow all should have access."""
    result = await access_control_action.has_action_access(
        user_id="user_xyz", action_label="SomeAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_deny_allows_admin(mock_save, access_control_action):
    """Admin user should have access to PersonaAction despite deny all."""
    result = await access_control_action.has_action_access(
        user_id="user_abc", action_label="PersonaAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_deny_non_admin(mock_save, access_control_action):
    """Non-admin user should be denied PersonaAction."""
    result = await access_control_action.has_action_access(
        user_id="user_xyz", action_label="PersonaAction", channel="default"
    )
    assert result is False


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_exception(mock_save, access_control_action):
    """Actions in exceptions list should always allow."""
    result = await access_control_action.has_action_access(
        user_id="user_xyz", action_label="ConverseInteractAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_action_alias(mock_save, access_control_action):
    """Action aliases should resolve to class name for lookup."""
    result = await access_control_action.has_action_access(
        user_id="user_abc", action_label="persona", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_whatsapp_specific_user(
    mock_save, access_control_action
):
    """WhatsApp channel allows only specific user."""
    result = await access_control_action.has_action_access(
        user_id="5926431530", action_label="any", channel="whatsapp"
    )
    assert result is True

    result = await access_control_action.has_action_access(
        user_id="other_user", action_label="any", channel="whatsapp"
    )
    assert result is False


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_empty_user_id_denies(mock_save, access_control_action):
    """Empty user_id denies when allow_anonymous is False (default)."""
    result = await access_control_action.has_action_access(
        user_id="", action_label="PersonaAction", channel="default"
    )
    assert result is False


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_has_action_access_allow_anonymous(mock_save, access_control_action):
    """When allow_anonymous is True, empty user_id bypasses rule evaluation."""
    access_control_action.allow_anonymous = True
    result = await access_control_action.has_action_access(
        user_id="", action_label="PersonaAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_enforce_false_allows_all(mock_save, access_control_action):
    """When enforce is False, allow regardless of permissions."""
    access_control_action.enforce = False
    result = await access_control_action.has_action_access(
        user_id="user_xyz", action_label="PersonaAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_graph_disabled_allows(mock_save, access_control_action):
    """When graph node is disabled, policy does not apply."""
    access_control_action.enabled = False
    result = await access_control_action.has_action_access(
        user_id="user_xyz", action_label="PersonaAction", channel="default"
    )
    assert result is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_report_denied_on_default_channel(mock_save):
    """ReportInterviewInteractAction denied on default when permissions specify deny all."""
    action = AccessControlAction(
        permissions={
            "default": {
                "any": {"deny": [], "allow": [{"group": "all", "enabled": True}]},
                "ReportInterviewInteractAction": {
                    "deny": [{"group": "all"}],
                    "allow": [],
                },
            },
        },
        user_groups={},
        default_deny=False,
    )
    action.enabled = True
    action.enforce = True

    result = await action.has_action_access(
        user_id="user_xyz",
        action_label="ReportInterviewInteractAction",
        channel="default",
    )
    assert result is False


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_default_deny_when_no_rule_matches(mock_save):
    """When default_deny=True and no rule matches, should deny."""
    action = AccessControlAction(
        permissions={"default": {"any": {"deny": [], "allow": []}}},
        user_groups={},
        default_deny=True,
    )
    action.enabled = True
    action.enforce = True

    result = await action.has_action_access(
        user_id="user_xyz", action_label="SomeAction", channel="default"
    )
    assert result is False


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_user_group(mock_save, access_control_action):
    """add_user_group creates group and optionally seeds users."""
    await access_control_action.add_user_group("support", ["u1", "u2"])
    assert "support" in access_control_action.user_groups
    assert access_control_action.user_groups["support"] == ["u1", "u2"]
    mock_save.assert_called()


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_user_to_group(mock_save, access_control_action):
    """add_user_to_group appends user."""
    await access_control_action.add_user_to_group("admins", "user_new")
    assert "user_new" in access_control_action.user_groups["admins"]
    mock_save.assert_called()


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_user_to_group_idempotent(mock_save, access_control_action):
    """add_user_to_group is no-op if user already in group."""
    await access_control_action.add_user_to_group("admins", "user_abc")
    assert access_control_action.user_groups["admins"].count("user_abc") == 1


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_remove_user_from_group(mock_save, access_control_action):
    """remove_user_from_group removes user."""
    await access_control_action.remove_user_from_group("admins", "user_abc")
    assert "user_abc" not in access_control_action.user_groups["admins"]
    mock_save.assert_called()


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_remove_user_group(mock_save, access_control_action):
    """remove_user_group deletes group."""
    await access_control_action.remove_user_group("admins")
    assert "admins" not in access_control_action.user_groups
    mock_save.assert_called()


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_user_to_allow(mock_save, access_control_action):
    """add_user_to_allow adds user rule to allow list."""
    await access_control_action.add_user_to_allow(
        "default", "ReportAction", "user_report"
    )
    entry = access_control_action.permissions["default"]["ReportAction"]
    assert any(r.get("user") == "user_report" for r in entry["allow"])


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_user_to_deny(mock_save, access_control_action):
    """add_user_to_deny adds user rule to deny list."""
    await access_control_action.add_user_to_deny("default", "DeleteAction", "user_bad")
    entry = access_control_action.permissions["default"]["DeleteAction"]
    assert any(r.get("user") == "user_bad" for r in entry["deny"])


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_add_group_to_allow(mock_save, access_control_action):
    """add_group_to_allow adds group rule."""
    await access_control_action.add_group_to_allow(
        "default", "PersonaAction", "support_team"
    )
    entry = access_control_action.permissions["default"]["PersonaAction"]
    assert any(r.get("group") == "support_team" for r in entry["allow"])


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_remove_user_from_permission(mock_save, access_control_action):
    """remove_user_from_permission removes user rule."""
    await access_control_action.add_user_to_allow("default", "X", "u1")
    await access_control_action.remove_user_from_permission(
        "default", "X", "u1", from_allow=True
    )
    entry = access_control_action.permissions["default"]["X"]
    assert not any(r.get("user") == "u1" for r in entry["allow"])


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_get_user_groups_returns_copy(mock_save, access_control_action):
    """get_user_groups returns copy, not reference."""
    groups = access_control_action.get_user_groups()
    assert groups == {"admins": ["user_abc", "user_def"]}
    groups["admins"].append("mutate")
    assert "mutate" not in access_control_action.user_groups["admins"]


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_export_config_includes_user_groups(mock_save, access_control_action):
    """export_config returns user_groups not session_groups."""
    config = access_control_action.export_config()
    assert "user_groups" in config
    assert "session_groups" not in config
    assert config["user_groups"] == {"admins": ["user_abc", "user_def"]}
    assert "default_deny" in config
    assert "action_aliases" in config
    assert "enforce" in config
    assert "allow_anonymous" in config
    assert config["default_deny"] is True
    assert config["enforce"] is True


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_import_config_purge_roundtrip(mock_save, access_control_action):
    """Purge import restores default_deny, aliases, enforce, allow_anonymous."""
    exported = access_control_action.export_config()
    fresh = AccessControlAction()
    await fresh.import_config(exported, purge=True)
    assert fresh.permissions == access_control_action.permissions
    assert fresh.default_deny == access_control_action.default_deny
    assert fresh.action_aliases == access_control_action.action_aliases
    assert fresh.enforce == access_control_action.enforce
    assert fresh.allow_anonymous == access_control_action.allow_anonymous


@pytest.mark.asyncio
@patch(f"{_ACTION_MODULE}.AccessControlAction.save", new_callable=AsyncMock)
async def test_import_config_merge_dedupes_exceptions(mock_save):
    """Merge import does not duplicate exception entries."""
    action = AccessControlAction(exceptions=["A"], enforce=True, enabled=True)
    await action.import_config({"exceptions": ["A", "B"]}, purge=False)
    assert action.exceptions == ["A", "B"]


@pytest.mark.asyncio
@patch("jvagent.action.base.Action.find", new_callable=AsyncMock)
async def test_agent_get_access_control_action_uses_first_when_multiple(mock_find):
    """Multiple AccessControlAction nodes: first returned, error logged."""
    from jvagent.core.agent import Agent

    a1, a2 = MagicMock(), MagicMock()
    a1.id = "ac1"
    a2.id = "ac2"
    mock_find.return_value = [a1, a2]
    agent = Agent(
        namespace="jvagent",
        name="t",
        id="agent1",
        alias="",
        description="",
    )
    got = await agent.get_access_control_action()
    assert got is a1
    mock_find.assert_awaited_once()
