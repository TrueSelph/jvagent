# Google Sheets Action

Exposes Google Sheets operations (read, write, append, format, merge, share) via the Google Sheets API. **Login is MCP OAuth** (`MCPOAuthToken`), not `GoogleToken` / `/api/google/{action_id}`.

## Requirements

- **Google Cloud project** with Sheets API (and Drive, for share/delete) enabled
- **OAuth 2.0 Client ID** (Web application) with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`
- **`GOOGLE_CLIENT_SECRETS_JSON`** in `.env` (path or JSON string)
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`google_workspace`)

## Create credentials

This is an **OAuth client JSON** (client id + secret). It is **not** a service-account key from [IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) (APIs & Services → Credentials). Do **not** use IAM & Admin → Service Accounts.
2. Select or create the Google Cloud project.
3. Enable **Google Sheets API** and **Google Drive API** (Drive is required for share/delete): [APIs & Services → Library](https://console.cloud.google.com/apis/library).
4. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose Internal or External as appropriate. If the app is in Testing, add the Google accounts that will sign in as test users.
5. Click **Create credentials** → **OAuth client ID**.
6. Application type: **Web application**. Name it (for example `jvagent-mcp`).
7. Under **Authorized redirect URIs**, add exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin). Do **not** use the old `/api/google/callback/` path.
8. Click **Create**. Download the JSON (client id + secret).
9. Put the file path or the JSON contents in `.env` as `GOOGLE_CLIENT_SECRETS_JSON`, and set `JVAGENT_PUBLIC_BASE_URL` (see below).
10. Authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=sheets`.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
# Path to the downloaded OAuth client JSON:
GOOGLE_CLIENT_SECRETS_JSON=/absolute/path/to/client_secret.json
# Or paste the file contents as a single-line JSON string:
# GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"...","client_secret":"...","redirect_uris":["https://your-public-host.example/api/mcp/google_workspace/auth/callback"]}}
```

Optional default spreadsheet for tools that omit a URL:

```bash
GOOGLE_SHEETS_SPREADSHEET_URL=https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit
```

`redirect_uris` in the downloaded JSON / Cloud Console must be the **MCP** callback (`/api/mcp/google_workspace/auth/callback`), not `/api/google/callback/`.

## Configuration

| Attribute          | Description                                                          | Required |
| ------------------ | -------------------------------------------------------------------- | -------- |
| `spreadsheet_url`  | Default spreadsheet URL or id when tools omit one                    | No       |
| `worksheet_title`  | Default tab name when a range has no sheet qualifier                 | No       |
| `auth_url`         | Set on startup to `/api/mcp/google_workspace/auth?account=integral&service=sheets` | —        |

Other Google actions (Gmail, Docs, Drive, Calendar) use the same MCP login with their own `?service=`.

## Agent wiring (agent.yaml)

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

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env`. Authorize once at **`/api/mcp/google_workspace/auth?service=sheets`**.

The 14 `google_sheets__*` tools stay on this action. Gate them with orchestrator `skill_only_tools: ["google_sheets__*"]` and a `google_sheets` skill `allowed-tools` list when they should not sit on the default surface.

## Endpoints

Admin REST handlers under `/actions/{action_id}/...` (read, update, append, …) still call the same Python methods. They now authenticate with the MCP OAuth token.

### Authorization

1. Open `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=sheets`.
2. Complete Google consent. The callback stores `MCPOAuthToken` and hydrates google-workspace-mcp XDG credential files.
3. Sheets tools and the MCP stdio server both use that token. Re-auth if refresh fails.


### Read

```http
GET /agents/{agent_id}/google_sheets/read?spreadsheet_id=1abc...&range_name=Sheet1!A1:D10
```

### Write (Overwrite)

```json
POST /agents/{agent_id}/google_sheets/write
{
  "spreadsheet_id": "1abc...",
  "range_name": "Sheet1!A1:B2",
  "values": [
    ["Header 1", "Header 2"],
    ["Data 1", "Data 2"]
  ]
}
```

### Append

```json
POST /agents/{agent_id}/google_sheets/append
{
  "spreadsheet_id": "1abc...",
  "range_name": "Sheet1!A:A",
  "values": [
    ["New Row Data 1", "New Row Data 2"]
  ]
}
```
