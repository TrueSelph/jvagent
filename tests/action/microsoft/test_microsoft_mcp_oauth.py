"""MicrosoftAction subclasses read MCPOAuthToken instead of MicrosoftToken."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jvagent.action.microsoft.microsoft_excel_action.microsoft_excel_action import (
    MicrosoftExcelAction,
)
from jvagent.action.microsoft.microsoft_onedrive_action.microsoft_onedrive_action import (
    MicrosoftOneDriveAction,
)
from jvagent.action.microsoft.microsoft_outlook_calendar_action.microsoft_outlook_calendar_action import (
    MicrosoftOutlookCalendarAction,
)
from jvagent.action.microsoft.microsoft_outlook_mail_action.microsoft_outlook_mail_action import (
    MicrosoftOutlookMailAction,
)

_ACTIONS = (
    MicrosoftOutlookMailAction,
    MicrosoftOutlookCalendarAction,
    MicrosoftOneDriveAction,
    MicrosoftExcelAction,
)


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_get_access_token_uses_mcp_oauth_token(cls):
    action = cls()
    token = {
        "type": "authorized_user",
        "refresh_token": "rtok",
        "token": "atok",
        "client_id": "cid",
        "token_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "email": "ops@example.com",
    }

    with (
        patch.object(
            action,
            "_load_mcp_token",
            new=AsyncMock(return_value=("ops@example.com", token, None)),
        ),
        patch(
            "jvagent.action.mcp_oauth.microsoft_hydrate.microsoft_access_token_expired",
            return_value=False,
        ),
    ):
        assert await action._get_access_token() == "atok"


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_get_access_token_raises_with_mcp_auth_url(cls):
    action = cls()
    with (
        patch.object(
            action, "_load_mcp_token", new=AsyncMock(return_value=(None, None, None))
        ),
        patch(
            "jvagent.action.mcp_oauth.microsoft_hydrate.mcp_microsoft_365_auth_url",
            return_value=(
                "https://x/api/mcp/microsoft_365/auth"
                f"?account=integral&service={cls._MCP_SERVICE}"
            ),
        ),
    ):
        with pytest.raises(ValueError, match="/api/mcp/microsoft_365/auth"):
            await action._get_access_token()


@pytest.mark.parametrize("cls", _ACTIONS)
@pytest.mark.asyncio
async def test_save_mcp_token_writes_mcp_oauth_not_microsoft_token(cls):
    action = cls()
    oauth = AsyncMock()
    oauth.save_oauth_token_for_service = AsyncMock()
    payload = {
        "token": "atok",
        "refresh_token": "rtok",
        "client_id": "cid",
    }

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
        await action._save_mcp_token(payload)

    oauth.save_oauth_token_for_service.assert_awaited_once()
    args = oauth.save_oauth_token_for_service.await_args.args
    assert args[0] == "microsoft_365"
    assert args[1] == "ops@example.com"
    assert args[2]["refresh_token"] == "rtok"
    assert args[2]["token"] == "atok"
    assert oauth.save_oauth_token_for_service.await_args.kwargs.get("service") == (
        cls._MCP_SERVICE or None
    )


@pytest.mark.asyncio
async def test_outlook_action_uses_outlook_token_not_first_row():
    action = MicrosoftOutlookMailAction()
    outlook_tok = {
        "email": "a@example.com",
        "refresh_token": "rt-a",
        "mcp_services": ["outlook"],
    }
    drive_tok = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "mcp_services": ["onedrive"],
    }
    oauth = MagicMock()
    oauth.list_oauth_tokens = AsyncMock(
        return_value=[
            {"account_name": "b@example.com", "token": drive_tok, "node": None},
            {"account_name": "a@example.com", "token": outlook_tok, "node": None},
        ]
    )
    with patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)):
        account, token, _node = await action._load_mcp_token()
    assert account == "a@example.com"
    assert token is outlook_tok


@pytest.mark.asyncio
async def test_outlook_does_not_load_onedrive_token():
    action = MicrosoftOutlookMailAction()
    drive_tok = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "scopes": ["Files.ReadWrite.All"],
        "mcp_services": ["onedrive"],
    }
    oauth = MagicMock()
    oauth.list_oauth_tokens = AsyncMock(
        return_value=[
            {"account_name": "b@example.com", "token": drive_tok, "node": None},
        ]
    )
    with patch.object(action, "_mcp_oauth_action", new=AsyncMock(return_value=oauth)):
        account, token, _node = await action._load_mcp_token()
    assert account is None
    assert token is None


def test_outlook_refresh_scopes_drop_files():
    action = MicrosoftOutlookMailAction()
    token = {
        "refresh_token": "rtok",
        "scopes": [
            "openid",
            "offline_access",
            "User.Read",
            "Mail.Read",
            "Mail.Send",
            "Mail.ReadWrite",
            "Files.ReadWrite.All",
        ],
    }
    assert action._scopes_for_refresh(token) == [
        "offline_access",
        "User.Read",
        "Mail.Read",
        "Mail.ReadWrite",
        "Mail.Send",
    ]


def test_onedrive_refresh_scopes_drop_mail():
    action = MicrosoftOneDriveAction()
    token = {
        "refresh_token": "rtok",
        "scopes": [
            "offline_access",
            "User.Read",
            "Mail.Send",
            "Files.ReadWrite.All",
        ],
    }
    assert action._scopes_for_refresh(token) == [
        "offline_access",
        "User.Read",
        "Files.ReadWrite.All",
    ]


@pytest.mark.asyncio
async def test_refresh_microsoft_token_sends_service_scopes(monkeypatch):
    from jvagent.action.mcp_oauth.microsoft_hydrate import refresh_microsoft_365_token

    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expires_in": 3600,
                "scope": "Mail.Send Mail.Read",
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

    monkeypatch.setattr(
        "jvagent.action.mcp_oauth.microsoft_hydrate.httpx.AsyncClient",
        _Client,
    )
    updated = await refresh_microsoft_365_token(
        {
            "refresh_token": "old-rt",
            "client_id": "cid",
            "token_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        },
        scopes=["Mail.Send", "Mail.Read"],
    )
    assert captured["data"]["scope"] == "Mail.Send Mail.Read"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert updated["token"] == "new-at"
    assert updated["refresh_token"] == "new-rt"
