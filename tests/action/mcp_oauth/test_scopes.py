"""MCP OAuth scope resolution from google-workspace-mcp tool filters."""

import pytest

from jvagent.action.mcp_oauth.scopes import (
    GOOGLE_SCOPES,
    IDENTITY_SCOPES,
    GoogleOAuthServiceNotEnabled,
    describe_google_oauth_access,
    google_workspace_tool_config,
    granted_scopes_from_token_response,
    resolve_google_oauth_scopes,
)

SHEETS = "https://www.googleapis.com/auth/spreadsheets"
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
DRIVE = "https://www.googleapis.com/auth/drive"
DOCS = "https://www.googleapis.com/auth/documents"
GMAIL = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SETTINGS = "https://www.googleapis.com/auth/gmail.settings.basic"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR = "https://www.googleapis.com/auth/calendar"
PRESENTATIONS = "https://www.googleapis.com/auth/presentations"
FORMS_BODY = "https://www.googleapis.com/auth/forms.body"
FORMS_RESPONSES = "https://www.googleapis.com/auth/forms.responses.readonly"


def test_sheets_only_tools_request_sheets_and_drive_file():
    scopes = resolve_google_oauth_scopes(["manage_sheets"])
    assert scopes[:2] == IDENTITY_SCOPES
    assert SHEETS in scopes
    assert DRIVE_FILE in scopes
    unused = (
        GMAIL,
        GMAIL_SETTINGS,
        DOCS,
        CALENDAR,
        DRIVE,
        PRESENTATIONS,
        FORMS_BODY,
        FORMS_RESPONSES,
    )
    for extra in unused:
        assert extra not in scopes


def test_all_tools_include_workspace_apis_but_not_slides_or_forms():
    scopes = resolve_google_oauth_scopes("-all")
    assert scopes == GOOGLE_SCOPES
    for needed in (SHEETS, DRIVE, DRIVE_FILE, DOCS, GMAIL, GMAIL_SETTINGS, CALENDAR):
        assert needed in scopes
    for unused in (PRESENTATIONS, FORMS_BODY, FORMS_RESPONSES):
        assert unused not in scopes


def test_denied_tools_removes_matching_service():
    scopes = resolve_google_oauth_scopes("-all", denied_tools=["manage_email"])
    assert GMAIL not in scopes
    assert GMAIL_SETTINGS not in scopes
    assert SHEETS in scopes
    assert CALENDAR in scopes


def test_denied_tools_glob():
    scopes = resolve_google_oauth_scopes("-all", denied_tools=["manage_cal*"])
    assert CALENDAR not in scopes
    assert GMAIL in scopes


def test_service_gmail_with_all_is_gmail_plus_identity():
    scopes = resolve_google_oauth_scopes("-all", service="gmail")
    assert scopes[:2] == IDENTITY_SCOPES
    assert GMAIL in scopes
    assert GMAIL_SETTINGS in scopes
    for extra in (SHEETS, DOCS, CALENDAR, DRIVE, DRIVE_FILE):
        assert extra not in scopes


def test_service_gmail_with_sheets_only_raises():
    with pytest.raises(GoogleOAuthServiceNotEnabled, match="gmail"):
        resolve_google_oauth_scopes(["manage_sheets"], service="gmail")


def test_service_gmail_allowed_when_gmail_action_enabled():
    scopes = resolve_google_oauth_scopes(
        ["manage_sheets"],
        service="gmail",
        enabled_services={"gmail"},
    )
    assert scopes[:2] == IDENTITY_SCOPES
    assert GMAIL in scopes
    assert GMAIL_SEND in scopes
    assert GMAIL_READONLY in scopes
    assert GMAIL_SETTINGS in scopes
    for extra in (SHEETS, DOCS, CALENDAR, DRIVE):
        assert extra not in scopes


def test_service_sheets_excludes_gmail_even_when_gmail_action_enabled():
    scopes = resolve_google_oauth_scopes(
        ["manage_sheets"],
        service="sheets",
        enabled_services={"gmail"},
    )
    assert SHEETS in scopes
    assert DRIVE_FILE in scopes
    for extra in (GMAIL, GMAIL_SEND, GMAIL_READONLY, GMAIL_SETTINGS, DOCS, CALENDAR):
        assert extra not in scopes


def test_unknown_service_raises():
    with pytest.raises(GoogleOAuthServiceNotEnabled, match="Unknown Google service"):
        resolve_google_oauth_scopes("-all", service="slides")


def test_google_workspace_tool_config_reads_servers_list():
    tools, denied = google_workspace_tool_config(
        [
            {
                "name": "google_workspace",
                "tools": ["manage_sheets"],
                "denied_tools": ["manage_email"],
            }
        ]
    )
    assert tools == ["manage_sheets"]
    assert denied == ["manage_email"]


def test_google_workspace_tool_config_defaults_when_missing():
    tools, denied = google_workspace_tool_config([])
    assert tools == "-all"
    assert denied == []


def test_granted_scopes_prefers_google_scope_string():
    fallback = [SHEETS]
    got = granted_scopes_from_token_response({"scope": f"openid {SHEETS}"}, fallback)
    assert got == ["openid", SHEETS]


def test_granted_scopes_falls_back_when_missing():
    fallback = list(IDENTITY_SCOPES) + [SHEETS]
    assert granted_scopes_from_token_response({}, fallback) == fallback


def test_describe_sheets_only():
    text = describe_google_oauth_access(IDENTITY_SCOPES + [SHEETS, DRIVE_FILE])
    assert "Google Sheets" in text
    assert "Gmail" not in text
    assert "Calendar" not in text
