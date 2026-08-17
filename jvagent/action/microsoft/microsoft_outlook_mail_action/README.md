# Microsoft Outlook Mail Action

Send mail and list messages in the signed-in user’s mailbox via **Microsoft Graph** (`/me/sendMail`, `/me/messages`). **Login is MCP OAuth** (`MCPOAuthToken`), not `MicrosoftToken` / `/api/microsoft/{action_id}`.

Shared setup (environment variables, redirect URI, OAuth pages) is documented in the [Microsoft actions README](../README.md).

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
5. Open **API permissions** → **Microsoft Graph** → **Delegated** permissions. Add `offline_access`, `User.Read`, `Mail.Read`, `Mail.ReadWrite`, and `Mail.Send`. Grant admin consent if the tenant requires it.
6. From the app **Overview**, copy **Application (client) ID** and **Directory (tenant) ID**.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=common
```

Then authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth?service=outlook`.

## Microsoft Graph scopes

Delegated scopes requested at sign-in:

- `offline_access`
- `User.Read`
- `Mail.Read`
- `Mail.ReadWrite`
- `Mail.Send`

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

- action: jvagent/microsoft_outlook_mail_action
```

Authorize at **`/api/mcp/microsoft_365/auth?service=outlook`**. EmailAction `provider: outlook` uses this action's Graph token — the same MCP token.

## REST API (unified mail)

Paths assume the default `/api` prefix. Routes require authenticated **admin** access (see OpenAPI for auth).

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/api/actions/{action_id}/send` | Send email: `to`, `subject`, `body`; optional `user_id` (ignored; uses `/me`) |
| GET | `/api/actions/{action_id}/list` | List messages: `query` (Graph `$search`), `max_results`, optional `user_id` |
| GET | `/api/actions/{action_id}/profile` | Profile: `emailAddress`, `displayName` from `/me` |

### List response shape

Each message entry includes `id` and `threadId` (Graph `conversationId`).

### Send behavior

Body is sent as **plain text** (`contentType: Text`). Messages are saved to Sent Items.
