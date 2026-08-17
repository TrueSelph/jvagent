"""GoogleAction subclasses read MCPOAuthToken instead of GoogleToken."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.google.google_calendar_action.google_calendar_action import (
    GoogleCalendarAction,
)
from jvagent.action.google.google_docs_action.google_docs_action import GoogleDocsAction
from jvagent.action.google.google_drive_action.google_drive_action import (
    GoogleDriveAction,
)
from jvagent.action.google.google_gmail_action.google_gmail_action import (
    GoogleGmailAction,
)
from jvagent.action.google.google_sheets_action.google_sheets_action import (
    GoogleSheetsAction,
)

_ACTIONS = (
    GoogleSheetsAction,
    GoogleGmailAction,
    GoogleDocsAction,
    GoogleDriveAction,
    GoogleCalendarAction,
)


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_get_credentials_uses_mcp_oauth_token(cls):
    action = cls()
    token = {
        "type": "authorized_user",
        "refresh_token": "rtok",
        "client_id": "cid",
        "client_secret": "csecret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "email": "ops@example.com",
    }
    fake_creds = MagicMock()
    fake_creds.valid = True

    with (
        patch.object(
            action,
            "_load_mcp_token",
            new=AsyncMock(return_value=("ops@example.com", token, None)),
        ),
        patch.object(action, "_credentials_from_mcp_payload", return_value=fake_creds),
    ):
        creds = await action._get_credentials()
    assert creds is fake_creds


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_get_credentials_raises_with_mcp_auth_url(cls):
    action = cls()
    with (
        patch.object(
            action, "_load_mcp_token", new=AsyncMock(return_value=(None, None, None))
        ),
        patch(
            "jvagent.action.mcp_oauth.hydrate.mcp_google_workspace_auth_url",
            return_value=(
                "https://x/api/mcp/google_workspace/auth"
                f"?account=integral&service={cls._MCP_SERVICE}"
            ),
        ),
    ):
        with pytest.raises(ValueError, match="/api/mcp/google_workspace/auth"):
            await action._get_credentials()


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_save_credentials_writes_mcp_oauth_not_google_token(cls):
    action = cls()
    oauth = MagicMock()
    oauth.save_oauth_token_for_service = AsyncMock()
    creds = MagicMock()
    creds.token = "atok"
    creds.refresh_token = "rtok"
    creds.client_id = "cid"
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.scopes = list(cls.SCOPES)
    creds.expiry = None

    with (
        patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)),
        patch.object(
            action,
            "_load_mcp_token",
            new=AsyncMock(
                return_value=("ops@example.com", {"refresh_token": "old"}, None)
            ),
        ),
        patch(
            "jvagent.action.oauth.audit._audit_log_oauth_event",
            return_value=None,
        ),
    ):
        await action._save_credentials(creds)

    oauth.save_oauth_token_for_service.assert_awaited_once()
    args = oauth.save_oauth_token_for_service.await_args.args
    assert args[0] == "google_workspace"
    assert args[1] == "ops@example.com"
    assert args[2]["refresh_token"] == "rtok"
    assert args[2]["token"] == "atok"
    assert oauth.save_oauth_token_for_service.await_args.kwargs.get("service") == (
        cls._MCP_SERVICE or None
    )


@pytest.mark.asyncio
async def test_docs_load_credentials_aliases_get_credentials():
    action = GoogleDocsAction()
    fake_creds = MagicMock()
    with patch.object(
        action, "_get_credentials", new=AsyncMock(return_value=fake_creds)
    ):
        assert await action._load_credentials() is fake_creds


@pytest.mark.asyncio
async def test_sheets_action_uses_sheets_token_not_first_row():
    action = GoogleSheetsAction()
    sheets_tok = {
        "email": "a@example.com",
        "refresh_token": "rt-a",
        "mcp_services": ["sheets"],
    }
    drive_tok = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "mcp_services": ["drive"],
    }
    oauth = MagicMock()
    oauth.list_oauth_tokens = AsyncMock(
        return_value=[
            {"account_name": "b@example.com", "token": drive_tok, "node": None},
            {"account_name": "a@example.com", "token": sheets_tok, "node": None},
        ]
    )
    with patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)):
        account, token, _node = await action._load_mcp_token()
    assert account == "a@example.com"
    assert token is sheets_tok


@pytest.mark.asyncio
async def test_drive_action_uses_drive_token_not_first_row():
    action = GoogleDriveAction()
    sheets_tok = {
        "email": "a@example.com",
        "refresh_token": "rt-a",
        "mcp_services": ["sheets"],
    }
    drive_tok = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "mcp_services": ["drive"],
    }
    oauth = MagicMock()
    oauth.list_oauth_tokens = AsyncMock(
        return_value=[
            {"account_name": "a@example.com", "token": sheets_tok, "node": None},
            {"account_name": "b@example.com", "token": drive_tok, "node": None},
        ]
    )
    with patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)):
        account, token, _node = await action._load_mcp_token()
    assert account == "b@example.com"
    assert token is drive_tok


_MIXED_SCOPES = [
    "openid",
    "profile",
    "email",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
]


def test_drive_refresh_scopes_drop_openid_and_sheets():
    action = GoogleDriveAction()
    token = {
        "refresh_token": "rtok",
        "client_id": "cid",
        "client_secret": "csecret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": _MIXED_SCOPES,
    }
    with patch(
        "jvagent.action.google.google_action.Credentials.from_authorized_user_info"
    ) as mock_from:
        mock_from.return_value = MagicMock()
        action._credentials_from_mcp_payload(token)
    scopes = mock_from.call_args.args[1]
    assert scopes == ["https://www.googleapis.com/auth/drive"]
    assert mock_from.call_args.args[0]["scopes"] == scopes
    assert "openid" not in scopes


def test_sheets_refresh_scopes_keep_spreadsheets_and_drive_file():
    action = GoogleSheetsAction()
    token = {
        "refresh_token": "rtok",
        "client_id": "cid",
        "client_secret": "csecret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": " ".join(_MIXED_SCOPES),
    }
    with patch(
        "jvagent.action.google.google_action.Credentials.from_authorized_user_info"
    ) as mock_from:
        mock_from.return_value = MagicMock()
        action._credentials_from_mcp_payload(token)
    scopes = mock_from.call_args.args[1]
    assert scopes == [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    assert "openid" not in scopes
    assert "https://www.googleapis.com/auth/drive" not in scopes


def test_gmail_refresh_scopes_drop_sheets_and_openid():
    action = GoogleGmailAction()
    token = {
        "refresh_token": "rtok",
        "scopes": [
            *_MIXED_SCOPES,
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        ],
    }
    assert action._scopes_for_refresh(token) == [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]


def test_gmail_refresh_scopes_empty_when_only_sheets_granted():
    action = GoogleGmailAction()
    token = {"refresh_token": "rtok", "scopes": _MIXED_SCOPES}
    assert action._scopes_for_refresh(token) == []


def test_docs_refresh_scopes_keep_documents_and_drive_file():
    action = GoogleDocsAction()
    token = {
        "refresh_token": "rtok",
        "scopes": [
            *_MIXED_SCOPES,
            "https://www.googleapis.com/auth/documents",
        ],
    }
    assert action._scopes_for_refresh(token) == [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.file",
    ]


def test_calendar_refresh_scopes_keep_calendar_only():
    action = GoogleCalendarAction()
    token = {
        "refresh_token": "rtok",
        "scopes": [
            *_MIXED_SCOPES,
            "https://www.googleapis.com/auth/calendar",
        ],
    }
    assert action._scopes_for_refresh(token) == [
        "https://www.googleapis.com/auth/calendar"
    ]


def test_refresh_scopes_empty_when_token_has_no_scopes():
    action = GoogleDriveAction()
    assert action._scopes_for_refresh({"refresh_token": "rtok"}) == []


@pytest.mark.asyncio
async def test_gmail_does_not_load_sheets_token():
    action = GoogleGmailAction()
    sheets_tok = {
        "email": "a@example.com",
        "refresh_token": "rt-a",
        "scopes": [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
        ],
        "mcp_services": ["sheets"],
    }
    oauth = MagicMock()
    oauth.list_oauth_tokens = AsyncMock(
        return_value=[
            {"account_name": "a@example.com", "token": sheets_tok, "node": None},
        ]
    )
    with patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)):
        account, token, _node = await action._load_mcp_token()
    assert account is None
    assert token is None
