# Microsoft 365 actions

Microsoft 365 integrations use **Microsoft Entra ID** (OAuth 2.0) and **Microsoft Graph**. Login is **MCP OAuth** (`MCPOAuthToken`) at `/api/mcp/microsoft_365/auth` — not `MicrosoftToken` / `/api/microsoft/{action_id}`. All concrete actions subclass `MicrosoftAction`, which loads that token and exposes `graph_request` / `graph_json` against `https://graph.microsoft.com/v1.0`.

## Requirements

- **Entra ID app registration** (single- or multi-tenant as appropriate)
- **Redirect URI** `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth/callback`
- **Delegated API permissions** on Microsoft Graph that match the scopes each action requests (admin consent if required by your tenant)
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`microsoft_365`)

## Create credentials

1. Open [Entra ID → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
2. Click **New registration**. Choose single-tenant or multi-tenant (personal Microsoft accounts) as appropriate.
3. Under **Authentication**, add a **Web** redirect URI exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin).
4. Open **Certificates & secrets** → **New client secret**. Copy the secret value once (required for confidential web clients).
5. Open **API permissions** → **Microsoft Graph** → **Delegated** permissions. Add the scopes for the actions you use (see the packaged-actions table). Grant admin consent if the tenant requires it.
6. From the app **Overview**, copy **Application (client) ID** and **Directory (tenant) ID**.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=common
```

Then authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth?service=outlook` (or `calendar` / `onedrive` / `excel`).

## Environment variables

| Variable | Purpose |
| -------- | ------- |
| `MICROSOFT_CLIENT_ID` | Application (client) ID from Entra ID |
| `MICROSOFT_CLIENT_SECRET` | Client secret for **confidential** web clients (omit for public client flows that do not use a secret) |
| `MICROSOFT_TENANT_ID` | Tenant id, or `common` for multi-tenant / personal Microsoft accounts (default in code when unset) |
| `JVAGENT_PUBLIC_BASE_URL` | Public HTTPS origin of the API (no trailing path). Used to build MCP `auth_url` |
| `ONEDRIVE_PARENT_FOLDER_ID` | Optional default for OneDrive / Excel parent folder (`root` or a drive **item** id) |

On register, reload, and startup, each `MicrosoftAction` sets `auth_url` to `/api/mcp/microsoft_365/auth?account=integral&service=...`.

Register the MCP callback redirect URI exactly in Entra ID under the app’s **Authentication** redirect URIs.

## OAuth flow

1. Open the action’s **`auth_url`** (or `/api/mcp/microsoft_365/auth?service=outlook|calendar|onedrive|excel`).
2. After consent, Entra redirects to **`/api/mcp/microsoft_365/auth/callback`**. The handler stores tokens on `MCPOAuthToken` and the next `microsoft_365` stdio spawn injects `MS365_MCP_OAUTH_TOKEN`.

## Packaged actions

Each package includes its own `README.md` (endpoints, behavior, and any limits).

| Package | Class | Graph scopes (delegated) | `?service=` |
| ------- | ----- | ------------------------ | ----------- |
| `jvagent/microsoft_outlook_mail_action` | `MicrosoftOutlookMailAction` | `offline_access`, `User.Read`, `Mail.Read`, `Mail.ReadWrite`, `Mail.Send` | `outlook` |
| `jvagent/microsoft_outlook_calendar_action` | `MicrosoftOutlookCalendarAction` | `offline_access`, `User.Read`, `Calendars.ReadWrite` | `calendar` |
| `jvagent/microsoft_onedrive_action` | `MicrosoftOneDriveAction` | `offline_access`, `User.Read`, `Files.ReadWrite.All` | `onedrive` |
| `jvagent/microsoft_excel_action` | `MicrosoftExcelAction` | `offline_access`, `User.Read`, `Files.ReadWrite.All` | `excel` |

### `MicrosoftExcelAction` attributes

| Attribute | Description |
| --------- | ----------- |
| `spreadsheet_url` | Default workbook: OneDrive item id or sharing URL containing `/items/{id}` |
| `worksheet_title` | Default sheet tab name when a range omits an explicit sheet |

## Workspace REST API (per action)

Each Microsoft package registers **`endpoints.py`** when the package is imported (same pattern as Google workspace actions). Route paths match the Google analogs under **`/actions/{action_id}/...`**.

**jvspatial note:** only **one** handler is mounted per distinct `path` + HTTP method. If both a Google action package and a Microsoft action package register the same route, whichever module is imported **first** wins; the other registration is skipped. Load the providers you need and rely on consistent import order if you use both families in one process.

Paths below assume the default **`/api`** prefix (see OpenAPI for full schemas).

**Mail** (`MicrosoftOutlookMailAction`):

| Method | Path | Notes |
| ------ | ---- | ----- |
| POST | `/api/actions/{action_id}/send` | `to`, `subject`, `body`, optional `user_id` |
| GET | `/api/actions/{action_id}/list` | Mailbox messages; `query`, `max_results`, `user_id` |
| GET | `/api/actions/{action_id}/profile` | Signed-in user mail profile |

**Drive** (`MicrosoftOneDriveAction`):

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET | `/api/actions/{action_id}/list` | `folder_id`, `with_link` |
| POST | `/api/actions/{action_id}/upload` | `name`, optional `content` (base64), `source_url`, `mime_type`, `parent_folder_id` |
| DELETE | `/api/actions/{action_id}/delete` | `file_id` |
| POST | `/api/actions/{action_id}/share` | `file_id`, `share_type`, etc. |
| POST | `/api/actions/{action_id}/compare_files` | Body listings diff |

**Calendar** (`MicrosoftOutlookCalendarAction`):

| Method | Path | Notes |
| ------ | ---- | ----- |
| GET | `/api/actions/{action_id}/list` | `calendar_id`, `time_min`, `max_results` |
| POST | `/api/actions/{action_id}/create` | `summary`, `start_time`, `end_time`, optional `calendar_id`, `description`, `location` |
| DELETE | `/api/actions/{action_id}/delete` | `calendar_id`, `event_id` |

**Excel** (`MicrosoftExcelAction`): workbook routes in `microsoft_excel_action/endpoints.py` cover **`delete`**, **`share`**, and **`create`**. The action class also implements Graph workbook operations similar to Sheets (`read_spreadsheet`, `update_spreadsheet`, etc.).

Authenticated admin routes typically require **`auth=True`** and role **`admin`**; refer to OpenAPI for full query/body schemas.

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
        args: ["-y", "@softeria/ms-365-mcp-server", "--org-mode"]
        tools: "-all"
        sandbox_mode: false

- action: jvagent/microsoft_outlook_mail_action
- action: jvagent/microsoft_outlook_calendar_action
- action: jvagent/microsoft_onedrive_action
- action: jvagent/microsoft_excel_action
  context:
    spreadsheet_url: ""
    worksheet_title: Sheet1
```

Set `MICROSOFT_CLIENT_ID` (and related env) in `.env`. Authorize once at **`/api/mcp/microsoft_365/auth?service=outlook`** (or the matching `?service=`).

## Implementation notes

- Tokens live on **`MCPOAuthToken`** (`server_name=microsoft_365`); access tokens are refreshed with Entra and saved back on that node.
- `MicrosoftAction.graph_request` accepts relative Graph paths or full URLs.
- `@softeria/ms-365-mcp-server` receives the current access token as `MS365_MCP_OAUTH_TOKEN` on stdio spawn (BYOT; we refresh before inject).
- OneDrive uploads may send raw bytes to Graph with the appropriate `Content-Type`; folder creation uses a JSON body without file content.
