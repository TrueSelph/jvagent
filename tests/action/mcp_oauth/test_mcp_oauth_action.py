"""Per-service MCP OAuth setup list on MCPOAuthAction."""

from types import SimpleNamespace

from jvagent.action.mcp_oauth.endpoints import _redirect_uri_for_server
from jvagent.action.mcp_oauth.mcp_oauth_action import (
    apply_service_rebind,
    build_oauth_setup,
    mcp_oauth_state_action_id,
    oauth_bindings_from_tokens,
    parse_mcp_oauth_state_action_id,
)
from jvagent.action.mcp_oauth.scopes import google_services_from_scopes

BASE = "https://agent.example.com"
SHEETS_DRIVE_SERVERS = [
    {
        "name": "google_workspace",
        "tools": ["manage_sheets", "manage_drive"],
    }
]


def test_sheets_and_drive_tools_build_two_dicts():
    setup = build_oauth_setup(BASE, SHEETS_DRIVE_SERVERS)
    assert [item["service"] for item in setup] == ["sheets", "drive"]
    assert [item["label"] for item in setup] == ["Google Sheets", "Google Drive"]
    callback = f"{BASE}/api/mcp/google_workspace/auth/callback"
    assert {item["redirect_uri"] for item in setup} == {callback}
    assert setup[0]["auth_url"] == (
        f"{BASE}/api/mcp/google_workspace/auth?account=integral&service=sheets"
    )
    assert setup[1]["auth_url"] == (
        f"{BASE}/api/mcp/google_workspace/auth?account=integral&service=drive"
    )
    assert all(item["server"] == "google_workspace" for item in setup)


def test_no_public_base_url_returns_empty_list():
    assert build_oauth_setup("", SHEETS_DRIVE_SERVERS) == []
    assert build_oauth_setup("   ", SHEETS_DRIVE_SERVERS) == []


def test_redirect_uri_for_server_reads_from_oauth_setup():
    action = SimpleNamespace(
        oauth_setup=build_oauth_setup(BASE, SHEETS_DRIVE_SERVERS),
        redirect_uri="",
    )
    assert _redirect_uri_for_server(action, "google_workspace") == (
        f"{BASE}/api/mcp/google_workspace/auth/callback"
    )


def test_redirect_uri_for_server_falls_back_to_legacy_string(monkeypatch):
    action = SimpleNamespace(
        oauth_setup=[],
        redirect_uri=(
            "google_workspace: https://old.example/api/mcp/google_workspace/auth/callback"
        ),
    )
    monkeypatch.setattr(
        "jvagent.action.mcp_oauth.endpoints.get_public_base_url",
        lambda: "https://unused.example",
    )
    assert _redirect_uri_for_server(action, "google_workspace") == (
        "https://old.example/api/mcp/google_workspace/auth/callback"
    )


def test_unconfigured_google_workspace_is_generic_entry():
    setup = build_oauth_setup(BASE, [])
    assert len(setup) == 1
    assert setup[0]["server"] == "google_workspace"
    assert "service" not in setup[0]
    assert setup[0]["auth_url"] == (
        f"{BASE}/api/mcp/google_workspace/auth?account=integral"
    )


def test_enabled_google_action_without_matching_mcp_tool():
    setup = build_oauth_setup(
        BASE,
        [{"name": "google_workspace", "tools": ["manage_sheets"]}],
        google_enabled_services=["drive"],
    )
    assert [item["service"] for item in setup] == ["sheets", "drive"]


def test_microsoft_services_are_separate_dicts():
    setup = build_oauth_setup(
        BASE,
        [
            {"name": "google_workspace", "tools": ["manage_sheets"]},
            {"name": "microsoft_365", "tools": "-all"},
        ],
        microsoft_enabled_services=["outlook"],
    )
    google = [item for item in setup if item["server"] == "google_workspace"]
    microsoft = [item for item in setup if item["server"] == "microsoft_365"]
    assert [item["service"] for item in google] == ["sheets"]
    assert [item["service"] for item in microsoft] == [
        "outlook",
        "calendar",
        "onedrive",
        "excel",
    ]
    assert microsoft[0]["auth_url"].endswith("&service=outlook")
    assert microsoft[0]["redirect_uri"].endswith("/api/mcp/microsoft_365/auth/callback")


SHEETS_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def test_sheets_scopes_do_not_bind_drive():
    assert google_services_from_scopes(SHEETS_SCOPES) == ["sheets"]
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [
            {
                "account_name": "integral",
                "token": {"email": "ops@example.com", "scopes": SHEETS_SCOPES},
            }
        ],
    )
    assert bindings == {"sheets": {"email": "ops@example.com"}}
    assert "drive" not in bindings


def test_drive_scope_binds_drive_and_email_from_payload():
    scopes = [*SHEETS_SCOPES, DRIVE_SCOPE]
    assert google_services_from_scopes(scopes) == ["sheets", "drive"]
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [
            {
                "account_name": "integral",
                "token": {"email": "ops@example.com", "scopes": scopes},
            }
        ],
    )
    assert bindings == {
        "sheets": {"email": "ops@example.com"},
        "drive": {"email": "ops@example.com"},
    }


def test_bindings_email_falls_back_to_account_name():
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [
            {
                "account_name": "fallback@example.com",
                "token": {"scopes": SHEETS_SCOPES},
            }
        ],
    )
    assert bindings == {"sheets": {"email": "fallback@example.com"}}


def test_microsoft_files_scope_binds_onedrive_and_excel():
    bindings = oauth_bindings_from_tokens(
        "microsoft_365",
        [
            {
                "account_name": "ops@example.com",
                "token": {
                    "email": "ops@example.com",
                    "scopes": ["Mail.Read", "Files.ReadWrite.All"],
                },
            }
        ],
    )
    assert bindings == {
        "outlook": {"email": "ops@example.com"},
        "onedrive": {"email": "ops@example.com"},
        "excel": {"email": "ops@example.com"},
    }


def test_mcp_oauth_state_action_id_roundtrip():
    assert mcp_oauth_state_action_id("integral", "sheets") == (
        "mcp_oauth:integral:sheets"
    )
    assert parse_mcp_oauth_state_action_id("mcp_oauth:integral:sheets") == (
        "integral",
        "sheets",
    )
    assert parse_mcp_oauth_state_action_id("mcp_oauth:integral") == ("integral", "")
    assert parse_mcp_oauth_state_action_id("") == ("integral", "")


def test_drive_mcp_services_does_not_bind_sheets():
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [
            {
                "account_name": "b@example.com",
                "token": {
                    "email": "b@example.com",
                    "scopes": [*SHEETS_SCOPES, DRIVE_SCOPE],
                    "mcp_services": ["drive"],
                },
            }
        ],
    )
    assert bindings == {"drive": {"email": "b@example.com"}}


def test_rebind_drive_on_b_keeps_sheets_on_a():
    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-a",
                "scopes": SHEETS_SCOPES,
                "mcp_services": ["sheets"],
            },
        }
    ]
    incoming = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "scopes": ["openid", DRIVE_SCOPE],
        "mcp_services": ["drive"],
    }
    updates = apply_service_rebind(
        "google_workspace",
        rows,
        account_name="b@example.com",
        token_data=incoming,
        service="drive",
    )
    by_acc = dict(updates)
    assert by_acc["b@example.com"]["mcp_services"] == ["drive"]
    assert by_acc["b@example.com"]["refresh_token"] == "rt-b"
    assert "a@example.com" not in by_acc
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [rows[0], {"account_name": "b@example.com", "token": by_acc["b@example.com"]}],
    )
    assert bindings == {
        "sheets": {"email": "a@example.com"},
        "drive": {"email": "b@example.com"},
    }


def test_rebind_strips_drive_from_legacy_combined_token():
    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-a",
                "scopes": [*SHEETS_SCOPES, DRIVE_SCOPE],
            },
        }
    ]
    incoming = {
        "email": "b@example.com",
        "refresh_token": "rt-b",
        "scopes": [DRIVE_SCOPE],
        "mcp_services": ["drive"],
    }
    updates = apply_service_rebind(
        "google_workspace",
        rows,
        account_name="b@example.com",
        token_data=incoming,
        service="drive",
    )
    by_acc = dict(updates)
    assert by_acc["a@example.com"]["mcp_services"] == ["sheets"]
    assert by_acc["a@example.com"]["refresh_token"] == "rt-a"
    assert by_acc["b@example.com"]["mcp_services"] == ["drive"]
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [{"account_name": acc, "token": tok} for acc, tok in updates],
    )
    assert bindings == {
        "sheets": {"email": "a@example.com"},
        "drive": {"email": "b@example.com"},
    }


def test_same_email_keeps_per_service_refresh_tokens():
    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-old",
                "scopes": SHEETS_SCOPES,
                "mcp_services": ["sheets"],
            },
        }
    ]
    incoming = {
        "email": "a@example.com",
        "refresh_token": "rt-new",
        "scopes": [DRIVE_SCOPE],
        "mcp_services": ["drive"],
    }
    updates = apply_service_rebind(
        "google_workspace",
        rows,
        account_name="a@example.com",
        token_data=incoming,
        service="drive",
    )
    assert len(updates) == 1
    acc, tok = updates[0]
    assert acc == "a@example.com"
    assert tok["mcp_services"] == ["sheets", "drive"]
    assert tok["service_tokens"]["sheets"]["refresh_token"] == "rt-old"
    assert tok["service_tokens"]["drive"]["refresh_token"] == "rt-new"
    assert tok["refresh_token"] == "rt-new"
    assert tok["service_tokens"]["sheets"]["scopes"] == SHEETS_SCOPES
    assert tok["service_tokens"]["drive"]["scopes"] == [DRIVE_SCOPE]
    assert "https://www.googleapis.com/auth/spreadsheets" not in (
        tok["service_tokens"]["drive"]["scopes"]
    )
    bindings = oauth_bindings_from_tokens(
        "google_workspace",
        [{"account_name": acc, "token": tok}],
    )
    assert bindings == {
        "sheets": {"email": "a@example.com"},
        "drive": {"email": "a@example.com"},
    }


def test_gmail_does_not_use_sheets_only_token():
    from jvagent.action.mcp_oauth.mcp_oauth_action import token_row_for_service

    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-sheets",
                "scopes": SHEETS_SCOPES,
                "mcp_services": ["sheets"],
            },
        }
    ]
    account, token, _node = token_row_for_service(
        rows, "google_workspace", "gmail", "integral"
    )
    assert account is None
    assert token is None


def test_poisoned_merged_token_is_not_used_for_gmail():
    from jvagent.action.mcp_oauth.mcp_oauth_action import token_row_for_service

    gmail_scope = "https://www.googleapis.com/auth/gmail.send"
    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-sheets-only",
                "scopes": [*SHEETS_SCOPES, gmail_scope],
                "mcp_services": ["sheets", "gmail"],
            },
        }
    ]
    account, token, _node = token_row_for_service(
        rows, "google_workspace", "gmail", "integral"
    )
    assert account is None
    assert token is None


def test_token_row_returns_service_blob_not_sibling():
    from jvagent.action.mcp_oauth.mcp_oauth_action import token_row_for_service

    parent = {
        "email": "a@example.com",
        "client_id": "cid",
        "mcp_services": ["sheets", "gmail"],
        "last_authorized_service": "gmail",
        "refresh_token": "rt-gmail",
        "service_tokens": {
            "sheets": {
                "refresh_token": "rt-sheets",
                "scopes": SHEETS_SCOPES,
            },
            "gmail": {
                "refresh_token": "rt-gmail",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            },
        },
    }
    rows = [{"account_name": "a@example.com", "token": parent, "node": None}]
    account, sheets, _node = token_row_for_service(
        rows, "google_workspace", "sheets", "integral"
    )
    assert account == "a@example.com"
    assert sheets["refresh_token"] == "rt-sheets"
    assert sheets["client_id"] == "cid"
    assert "https://www.googleapis.com/auth/spreadsheets" in sheets["scopes"]
    _, gmail, _ = token_row_for_service(rows, "google_workspace", "gmail", "integral")
    assert gmail["refresh_token"] == "rt-gmail"
    assert gmail["scopes"] == ["https://www.googleapis.com/auth/gmail.send"]


def test_same_email_microsoft_keeps_outlook_and_onedrive_tokens():
    rows = [
        {
            "account_name": "a@example.com",
            "token": {
                "email": "a@example.com",
                "refresh_token": "rt-mail",
                "scopes": ["Mail.Send", "Mail.Read"],
                "mcp_services": ["outlook"],
            },
        }
    ]
    incoming = {
        "email": "a@example.com",
        "refresh_token": "rt-files",
        "scopes": ["Files.ReadWrite.All"],
        "mcp_services": ["onedrive"],
    }
    updates = apply_service_rebind(
        "microsoft_365",
        rows,
        account_name="a@example.com",
        token_data=incoming,
        service="onedrive",
    )
    assert len(updates) == 1
    _acc, tok = updates[0]
    assert tok["mcp_services"] == ["outlook", "onedrive"]
    assert tok["service_tokens"]["outlook"]["refresh_token"] == "rt-mail"
    assert tok["service_tokens"]["onedrive"]["refresh_token"] == "rt-files"
    assert "Files.ReadWrite.All" not in tok["service_tokens"]["outlook"]["scopes"]
    assert "Mail.Send" not in tok["service_tokens"]["onedrive"]["scopes"]
