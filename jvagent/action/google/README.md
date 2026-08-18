# Google Workspace actions

Google Workspace integrations use **OAuth 2.0 Client IDs** (Web application) and the Google APIs. Login is **MCP OAuth** (`MCPOAuthToken`) at `/api/mcp/google_workspace/auth` — not `GoogleToken` / `/api/google/{action_id}`. All concrete actions subclass `GoogleAction`, which loads that token and builds the Google API client.

This is an **OAuth client JSON** (client id + secret). It is **not** a service-account key from [IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

## Requirements

- **Google Cloud project** with the APIs for the actions you use enabled
- **OAuth 2.0 Client ID** (Web application) with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`
- **`GOOGLE_CLIENT_SECRETS_JSON`** in `.env` (path or JSON string)
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`google_workspace`)

## Create credentials

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) (APIs & Services → Credentials). Do **not** use IAM & Admin → Service Accounts.
2. Select or create the Google Cloud project.
3. Enable the APIs you need (see the packaged-actions table below). Library: [APIs & Services → Library](https://console.cloud.google.com/apis/library).
4. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose Internal or External as appropriate. If the app is in Testing, add the Google accounts that will sign in as test users.
5. Click **Create credentials** → **OAuth client ID**.
6. Application type: **Web application**. Name it (for example `jvagent-mcp`).
7. Under **Authorized redirect URIs**, add exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin). Do **not** use the old `/api/google/callback/` path.
8. Click **Create**. Download the JSON (client id + secret). This is the OAuth client file, not a service-account key.
9. Put the file path or the JSON contents in `.env` as `GOOGLE_CLIENT_SECRETS_JSON`, and set `JVAGENT_PUBLIC_BASE_URL` (see below).
10. Authorize at the action’s MCP URL, for example `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=sheets`.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
# Path to the downloaded OAuth client JSON:
GOOGLE_CLIENT_SECRETS_JSON=/absolute/path/to/client_secret.json
# Or paste the file contents as a single-line JSON string:
# GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"...","client_secret":"...","redirect_uris":["https://your-public-host.example/api/mcp/google_workspace/auth/callback"]}}
```

`redirect_uris` in the downloaded JSON / Cloud Console must be the **MCP** callback (`/api/mcp/google_workspace/auth/callback`), not `/api/google/callback/`.

On register, reload, and startup, each `GoogleAction` sets `auth_url` to `/api/mcp/google_workspace/auth?account=integral&service=...`.

## OAuth flow

1. Open the action’s **`auth_url`** (or `/api/mcp/google_workspace/auth?service=sheets|gmail|docs|drive|calendar`).
2. After consent, Google redirects to **`/api/mcp/google_workspace/auth/callback`**. The handler stores tokens on `MCPOAuthToken` and hydrates google-workspace-mcp XDG credential files.
3. In-process Google actions and the MCP stdio server both use that token. Re-auth if refresh fails.

## Packaged actions

Each package includes its own `README.md` (endpoints, behavior, credentials, and `.env`).

| Package | Class | APIs to enable | MCP tool | `?service=` |
| ------- | ----- | -------------- | -------- | ----------- |
| `jvagent/google_sheets_action` | `GoogleSheetsAction` | Sheets API, Drive API (share/delete) | `manage_sheets` | `sheets` |
| `jvagent/google_gmail_action` | `GoogleGmailAction` | Gmail API | `manage_email` | `gmail` |
| `jvagent/google_docs_action` | `GoogleDocsAction` | Docs API, Drive API | `manage_docs` | `docs` |
| `jvagent/google_drive_action` | `GoogleDriveAction` | Drive API | `manage_drive` | `drive` |
| `jvagent/google_calendar_action` | `GoogleCalendarAction` | Calendar API | `manage_calendar` | `calendar` |

Identity scopes (`openid`, userinfo email) are always requested. Service-specific Google API scopes are requested from `?service=` so a Sheets-only agent does not ask for Gmail, Docs, Drive, or Calendar.

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/mcp_oauth
  context:
    enabled: true

- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: google_workspace
        enabled: true
        transport: stdio
        command: npx
        args: ["-y", "@aaronsb/google-workspace-mcp"]
        tools: ["manage_sheets"]
        show_tools: false
        sandbox_mode: false

- action: jvagent/google_sheets_action
  context:
    worksheet_title: Sheet1
    spreadsheet_url: ${GOOGLE_SHEETS_SPREADSHEET_URL}
```

Enable only the MCP `tools` you need (`manage_sheets`, `manage_email`, `manage_docs`, `manage_drive`, `manage_calendar`). Authorize once at **`/api/mcp/google_workspace/auth?service=sheets`** (or the matching `?service=`).

## Implementation notes

- Tokens live on **`MCPOAuthToken`** (`server_name=google_workspace`); access tokens are refreshed with the OAuth client in `GOOGLE_CLIENT_SECRETS_JSON` and saved back on that node.
- `@aaronsb/google-workspace-mcp` hydrates XDG credential files from the same MCP token.
- There is no `@tool` skill for Docs; Sheets, Gmail, Drive, and Calendar have packaged skills under `jvagent/skills/google/`.
