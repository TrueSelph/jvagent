# Google Calendar Action

Exposes Google Calendar operations (list, create, delete) via the Calendar API. **Login is MCP OAuth** (`MCPOAuthToken`), not `GoogleToken` / `/api/google/{action_id}`.

## Requirements

- **Google Cloud project** with Calendar API enabled
- **OAuth 2.0 Client ID** (Web application) with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`
- **`GOOGLE_CLIENT_SECRETS_JSON`** in `.env`
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`google_workspace`)

## Create credentials

This is an **OAuth client JSON** (client id + secret). It is **not** a service-account key from [IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) (APIs & Services → Credentials). Do **not** use IAM & Admin → Service Accounts.
2. Select or create the Google Cloud project.
3. Enable **Google Calendar API**: [APIs & Services → Library](https://console.cloud.google.com/apis/library).
4. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose Internal or External as appropriate. If the app is in Testing, add the Google accounts that will sign in as test users.
5. Click **Create credentials** → **OAuth client ID**.
6. Application type: **Web application**. Name it (for example `jvagent-mcp`).
7. Under **Authorized redirect URIs**, add exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin). Do **not** use the old `/api/google/callback/` path.
8. Click **Create**. Download the JSON (client id + secret).
9. Put the file path or the JSON contents in `.env` as `GOOGLE_CLIENT_SECRETS_JSON`, and set `JVAGENT_PUBLIC_BASE_URL` (see below).
10. Authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=calendar`.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
# Path to the downloaded OAuth client JSON:
GOOGLE_CLIENT_SECRETS_JSON=/absolute/path/to/client_secret.json
# Or paste the file contents as a single-line JSON string:
# GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"...","client_secret":"...","redirect_uris":["https://your-public-host.example/api/mcp/google_workspace/auth/callback"]}}
```

`redirect_uris` in the downloaded JSON / Cloud Console must be the **MCP** callback (`/api/mcp/google_workspace/auth/callback`), not `/api/google/callback/`.

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
        tools: ["manage_calendar"]
        show_tools: false
        sandbox_mode: false

- action: jvagent/google_calendar_action
  context:
    enabled: true
```

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env`. Authorize at **`/api/mcp/google_workspace/auth?service=calendar`**.

## Endpoints

Admin REST handlers under `/actions/{action_id}/...` authenticate with the MCP OAuth token.

### Authorization

1. Open `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=calendar`.
2. Complete Google consent. The callback stores `MCPOAuthToken`.
3. Calendar tools use that token. Re-auth if refresh fails.

### List Events

```http
GET /agents/{agent_id}/google_calendar/list?calendar_id=primary&max_results=10
```

Omit `calendar_id` to use `default_calendar_id` or 'primary'.

### Create Event

```json
POST /agents/{agent_id}/google_calendar/create
{
  "calendar_id": "primary",
  "summary": "Team Meeting",
  "description": "Weekly sync",
  "start_time": "2026-03-10T10:00:00Z",
  "end_time": "2026-03-10T11:00:00Z"
}
```

### Delete Event

```json
DELETE /agents/{agent_id}/google_calendar/delete
{
  "calendar_id": "primary",
  "event_id": "abc123xyz"
}
```
