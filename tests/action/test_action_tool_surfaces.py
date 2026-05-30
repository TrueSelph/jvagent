"""Regression: core actions expose their full tool surface via get_tools()
(ADR-0012 — actions are first-class tools). Locks in the retrofit that let us
delete the redundant skill bundles. Each action is skipped gracefully if its
optional integration deps are absent in the test environment."""
from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.asyncio

# action import path -> (class name, expected tool names)
CASES = {
    "jvagent.action.google.google_gmail_action.google_gmail_action": (
        "GoogleGmailAction",
        {
            "gmail__send_email",
            "gmail__list_messages",
            "gmail__get_message",
            "gmail__mark_read",
            "gmail__get_profile",
        },
    ),
    "jvagent.action.google.google_calendar_action.google_calendar_action": (
        "GoogleCalendarAction",
        {"calendar__list_events", "calendar__create_event", "calendar__delete_event"},
    ),
    "jvagent.action.google.google_drive_action.google_drive_action": (
        "GoogleDriveAction",
        {
            "google_drive__list_files",
            "google_drive__upload_file",
            "google_drive__get_file_metadata",
            "google_drive__get_media",
            "google_drive__share_file",
            "google_drive__delete_file",
        },
    ),
    "jvagent.action.google.google_sheets_action.google_sheets_action": (
        "GoogleSheetsAction",
        {
            "google_sheets__read_spreadsheet",
            "google_sheets__update_spreadsheet",
            "google_sheets__append_spreadsheet",
            "google_sheets__create_spreadsheet",
            "google_sheets__delete_spreadsheet",
            "google_sheets__create_worksheet",
            "google_sheets__update_worksheet",
            "google_sheets__delete_worksheet",
            "google_sheets__merge_cells",
            "google_sheets__unmerge_cells",
            "google_sheets__format_cells",
            "google_sheets__last_filled_row",
            "google_sheets__batch_clear",
            "google_sheets__share_spreadsheet",
        },
    ),
    "jvagent.action.microsoft.microsoft_outlook_mail_action.microsoft_outlook_mail_action": (  # noqa: E501
        "MicrosoftOutlookMailAction",
        {
            "outlook__send_email",
            "outlook__list_messages",
            "outlook__list_inbox_messages",
            "outlook__get_message",
            "outlook__mark_read",
            "outlook__get_profile",
        },
    ),
    "jvagent.action.microsoft.microsoft_outlook_calendar_action.microsoft_outlook_calendar_action": (  # noqa: E501
        "MicrosoftOutlookCalendarAction",
        {
            "outlook_calendar__list_events",
            "outlook_calendar__create_event",
            "outlook_calendar__delete_event",
        },
    ),
    "jvagent.action.microsoft.microsoft_onedrive_action.microsoft_onedrive_action": (
        "MicrosoftOneDriveAction",
        {
            "onedrive__list_files",
            "onedrive__upload_file",
            "onedrive__share_file",
            "onedrive__delete_file",
        },
    ),
    "jvagent.action.microsoft.microsoft_excel_action.microsoft_excel_action": (
        "MicrosoftExcelAction",
        {
            "excel__read_spreadsheet",
            "excel__update_spreadsheet",
            "excel__append_spreadsheet",
            "excel__create_spreadsheet",
            "excel__delete_spreadsheet",
            "excel__create_worksheet",
            "excel__update_worksheet",
            "excel__delete_worksheet",
            "excel__batch_clear",
            "excel__share_spreadsheet",
        },
    ),
}


@pytest.mark.parametrize("module_path", list(CASES))
async def test_action_exposes_full_tool_surface(module_path):
    cls_name, expected = CASES[module_path]
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # optional integration deps absent
        pytest.skip(f"{cls_name} deps unavailable: {exc}")
    action = getattr(module, cls_name)()
    tools = await action.get_tools()
    names = {t.name for t in tools}
    assert names == expected
    # every tool must carry a callable executor and a valid object schema
    for t in tools:
        assert callable(getattr(t, "execute", None))
        assert t.parameters_schema.get("type") == "object"
