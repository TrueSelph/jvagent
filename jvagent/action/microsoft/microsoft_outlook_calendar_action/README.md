# Microsoft Outlook Calendar Action

List, create, and delete calendar events via **Microsoft Graph**. **Login is MCP OAuth** (`MCPOAuthToken`), not `MicrosoftToken` / `/api/microsoft/{action_id}`.

Shared setup is in the [Microsoft actions README](../README.md).

## Requirements

- **Entra ID app** with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth/callback`
- `MICROSOFT_CLIENT_ID` (and optional `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`)
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`microsoft_365`)

## Create credentials

1. Open [Entra ID → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**. Choose single-tenant or multi-tenant (personal Microsoft accounts) as appropriate.
3. Under **Authentication**, add a **Web** redirect URI exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin).
4. Open **Certificates & secrets** → **New client secret**. Copy the secret value once (required for confidential web clients).
5. Open **API permissions** → **Microsoft Graph** → **Delegated** permissions. Add `offline_access`, `User.Read`, and `Calendars.ReadWrite`. Grant admin consent if the tenant requires it.
6. From the app **Overview**, copy **Application (client) ID** and **Directory (tenant) ID**.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=common
```

Then authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth?service=calendar`.

## Microsoft Graph scopes

Delegated scopes:

- `offline_access`
- `User.Read`
- `Calendars.ReadWrite`

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/mcp_oauth
  context:
    enabled: true

- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: microsoft_365
        enabled: true
        transport: stdio
        command: npx
        args: ["-y", "@softeria/ms-365-mcp-server"]
        tools: "-all"
        sandbox_mode: false

- action: jvagent/microsoft_outlook_calendar_action
```

Authorize at **`/api/mcp/microsoft_365/auth?service=calendar`**.

## REST API (unified calendar)

Paths assume the default `/api` prefix. Admin-authenticated routes; see OpenAPI for parameters.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/actions/{action_id}/list` | List events: `calendar_id` (`primary` or calendar id), `time_min` (OData filter on `start/dateTime`), `max_results` |
| POST | `/api/actions/{action_id}/create` | Create event: `summary`, `start_time`, `end_time`, optional `calendar_id`, `description`, `location` |
| DELETE | `/api/actions/{action_id}/delete` | `calendar_id`, `event_id` |

## Behavior notes

- **`calendar_id`**: `primary` (default) uses `/me/events`; a specific id uses `/me/calendars/{id}/events`.
- **Times**: `create_event` sends `start` / `end` with `timeZone: "UTC"`; pass ISO-style `time` strings Graph accepts.
- **List shape**: Normalized fields include `id`, `summary` (subject), `start`, `end`, `location`, `description`, `webLink`.
