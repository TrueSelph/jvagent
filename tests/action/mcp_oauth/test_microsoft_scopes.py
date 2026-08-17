"""Microsoft 365 MCP OAuth scope resolution."""

import pytest

from jvagent.action.mcp_oauth.microsoft_scopes import (
    IDENTITY_SCOPES,
    MICROSOFT_SCOPES,
    MicrosoftOAuthServiceNotEnabled,
    describe_microsoft_oauth_access,
    microsoft_365_tool_config,
    resolve_microsoft_oauth_scopes,
)

MAIL = "Mail.Read"
MAIL_RW = "Mail.ReadWrite"
MAIL_SEND = "Mail.Send"
CAL = "Calendars.ReadWrite"
FILES = "Files.ReadWrite.All"


def test_all_tools_request_personal_graph_services():
    scopes = resolve_microsoft_oauth_scopes("-all")
    assert scopes[:3] == IDENTITY_SCOPES
    assert scopes == MICROSOFT_SCOPES
    for needed in (MAIL, MAIL_RW, MAIL_SEND, CAL, FILES):
        assert needed in scopes


def test_no_mcp_with_excel_action_only():
    scopes = resolve_microsoft_oauth_scopes(None, enabled_services={"excel"})
    assert IDENTITY_SCOPES[0] in scopes
    assert FILES in scopes
    assert MAIL not in scopes
    assert CAL not in scopes


def test_service_outlook_with_all():
    scopes = resolve_microsoft_oauth_scopes("-all", service="outlook")
    assert MAIL in scopes
    assert MAIL_SEND in scopes
    assert CAL not in scopes
    assert FILES not in scopes


def test_service_outlook_without_enablement_raises():
    with pytest.raises(MicrosoftOAuthServiceNotEnabled):
        resolve_microsoft_oauth_scopes(None, service="outlook")


def test_unknown_service_raises():
    with pytest.raises(MicrosoftOAuthServiceNotEnabled, match="Unknown"):
        resolve_microsoft_oauth_scopes("-all", service="teams")


def test_enabled_action_allows_service_without_mcp_all():
    scopes = resolve_microsoft_oauth_scopes(
        None, service="calendar", enabled_services={"calendar"}
    )
    assert CAL in scopes
    assert MAIL not in scopes


def test_microsoft_365_tool_config():
    tools, denied = microsoft_365_tool_config(
        [
            {"name": "google_workspace", "tools": "-all"},
            {"name": "microsoft_365", "tools": "-all", "denied_tools": []},
        ]
    )
    assert tools == "-all"
    assert denied == []
    tools2, _ = microsoft_365_tool_config([{"name": "google_workspace"}])
    assert tools2 is None


def test_describe_microsoft_oauth_access():
    text = describe_microsoft_oauth_access(MICROSOFT_SCOPES)
    assert "Outlook mail" in text
    assert "Outlook calendar" in text
    assert "OneDrive" in text
