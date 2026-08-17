"""XDG hydration for google-workspace-mcp credentials."""

import json

from jvagent.action.mcp_oauth.hydrate import (
    account_email_from_token,
    accounts_file_path,
    credential_path,
    email_to_slug,
    hydrate_google_workspace_account,
    mcp_google_workspace_auth_url,
)


def test_email_to_slug_matches_mcp_server():
    assert email_to_slug("user@gmail.com") == "user_at_gmail_dot_com"
    assert email_to_slug("a/b@x.com") == "ab_at_x_dot_com"


def test_hydrate_uses_last_authorized_service_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    token = {
        "type": "authorized_user",
        "client_id": "cid",
        "client_secret": "csecret",
        "email": "ops@example.com",
        "refresh_token": "rt-gmail",
        "last_authorized_service": "gmail",
        "mcp_services": ["sheets", "gmail"],
        "service_tokens": {
            "sheets": {
                "refresh_token": "rt-sheets",
                "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
            },
            "gmail": {
                "refresh_token": "rt-gmail",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            },
        },
    }
    hydrate_google_workspace_account("ops@example.com", token)

    cred = json.loads(credential_path("ops@example.com").read_text())
    assert cred["refresh_token"] == "rt-gmail"
    assert cred["scopes"] == ["https://www.googleapis.com/auth/gmail.send"]


def test_hydrate_writes_accounts_and_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    token = {
        "type": "authorized_user",
        "client_id": "cid",
        "client_secret": "csecret",
        "refresh_token": "rtok",
        "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
        "email": "ops@example.com",
    }
    hydrate_google_workspace_account("ops@example.com", token, category="work")

    registry = json.loads(accounts_file_path().read_text())
    assert registry["accounts"][0]["email"] == "ops@example.com"
    cred = json.loads(credential_path("ops@example.com").read_text())
    assert cred["type"] == "authorized_user"
    assert cred["refresh_token"] == "rtok"
    assert cred["client_id"] == "cid"


def test_hydrate_merges_existing_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    hydrate_google_workspace_account(
        "one@example.com",
        {
            "client_id": "a",
            "client_secret": "b",
            "refresh_token": "r1",
        },
    )
    hydrate_google_workspace_account(
        "two@example.com",
        {
            "client_id": "a",
            "client_secret": "b",
            "refresh_token": "r2",
        },
    )
    emails = {
        a["email"] for a in json.loads(accounts_file_path().read_text())["accounts"]
    }
    assert emails == {"one@example.com", "two@example.com"}


def test_account_email_prefers_payload():
    assert account_email_from_token({"email": "a@b.com"}, "integral") == "a@b.com"
    assert account_email_from_token({}, "integral") == "integral"


def test_mcp_auth_url(monkeypatch):
    monkeypatch.setattr(
        "jvagent.core.public_url.get_public_base_url",
        lambda: "https://agent.example.com",
    )
    assert mcp_google_workspace_auth_url() == (
        "https://agent.example.com/api/mcp/google_workspace/auth?account=integral"
    )
    assert mcp_google_workspace_auth_url(service="gmail") == (
        "https://agent.example.com/api/mcp/google_workspace/auth"
        "?account=integral&service=gmail"
    )


def test_mcp_microsoft_auth_url(monkeypatch):
    from jvagent.action.mcp_oauth.microsoft_hydrate import (
        mcp_microsoft_365_auth_url,
        merge_stdio_env,
        microsoft_365_stdio_env,
    )

    monkeypatch.setattr(
        "jvagent.core.public_url.get_public_base_url",
        lambda: "https://agent.example.com",
    )
    assert mcp_microsoft_365_auth_url() == (
        "https://agent.example.com/api/mcp/microsoft_365/auth?account=integral"
    )
    assert mcp_microsoft_365_auth_url(service="outlook") == (
        "https://agent.example.com/api/mcp/microsoft_365/auth"
        "?account=integral&service=outlook"
    )

    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")
    monkeypatch.delenv("MICROSOFT_CLIENT_SECRET", raising=False)
    overlay = microsoft_365_stdio_env({"token": "atok", "client_id": "cid"})
    assert overlay["MS365_MCP_OAUTH_TOKEN"] == "atok"
    assert overlay["MS365_MCP_CLIENT_ID"] == "cid"
    merged = merge_stdio_env(None, overlay)
    assert merged["MS365_MCP_OAUTH_TOKEN"] == "atok"
    assert "PATH" in merged
