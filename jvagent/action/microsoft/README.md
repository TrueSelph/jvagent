# Microsoft 365 actions

Microsoft 365 integrations use **Microsoft Entra ID** (OAuth 2.0) and **Microsoft Graph**. All concrete actions subclass `MicrosoftAction`, which handles authorization code flow with **PKCE**, token refresh, and `graph_request` / `graph_json` helpers against `https://graph.microsoft.com/v1.0`.

## Requirements

- **Entra ID app registration** (single- or multi-tenant as appropriate)
- **Redirect URI** matching the URL the agent computes at runtime (see below)
- **Delegated API permissions** on Microsoft Graph that match the scopes each action requests (admin consent if required by your tenant)

## Environment variables

| Variable | Purpose |
| -------- | ------- |
| `MICROSOFT_CLIENT_ID` | Application (client) ID from Entra ID |
| `MICROSOFT_CLIENT_SECRET` | Client secret for **confidential** web clients (omit for public client flows that do not use a secret) |
| `MICROSOFT_TENANT_ID` | Tenant id, or `common` for multi-tenant / personal Microsoft accounts (default in code when unset) |
| `JVAGENT_PUBLIC_BASE_URL` | Public HTTPS origin of the API (no trailing path). Used to build `auth_url` and `redirect_uri` |
| `ONEDRIVE_PARENT_FOLDER_ID` | Optional default for OneDrive / Excel parent folder (`root` or a drive **item** id) |

On register, reload, and startup, each `MicrosoftAction` sets:

- `auth_url` → `{JVAGENT_PUBLIC_BASE_URL}/api/microsoft/{action_id}`
- `redirect_uri` → `{JVAGENT_PUBLIC_BASE_URL}/api/microsoft/callback/`

Register **that** `redirect_uri` exactly in Entra ID under the app’s **Authentication** redirect URIs, and keep `JVAGENT_PUBLIC_BASE_URL` aligned with what you registered.

## OAuth flow

1. Open **`auth_url`** (or `GET /api/microsoft/{action_id}`) in a browser. The HTML page includes **Sign in with Microsoft**, which starts the Entra authorize URL with PKCE.
2. After consent, Entra redirects to **`/api/microsoft/callback/`** with `code` and `state`. The handler exchanges the code for tokens and stores them on a linked `MicrosoftToken` node.

If configuration is wrong, the auth page surfaces an error mentioning `MICROSOFT_CLIENT_ID`, optional `MICROSOFT_CLIENT_SECRET`, redirect registration, and `JVAGENT_PUBLIC_BASE_URL`.

## Packaged actions

Each package includes its own `README.md` (endpoints, behavior, and any limits).

| Package | Class | Graph scopes (delegated) |
| ------- | ----- | ------------------------ |
| `jvagent/microsoft_outlook_mail_action` | `MicrosoftOutlookMailAction` | `offline_access`, `User.Read`, `Mail.Read`, `Mail.Send` |
| `jvagent/microsoft_outlook_calendar_action` | `MicrosoftOutlookCalendarAction` | `offline_access`, `User.Read`, `Calendars.ReadWrite` |
| `jvagent/microsoft_onedrive_action` | `MicrosoftOneDriveAction` | `offline_access`, `User.Read`, `Files.ReadWrite.All` |
| `jvagent/microsoft_excel_action` | `MicrosoftExcelAction` | `offline_access`, `User.Read`, `Files.ReadWrite.All` |

### `MicrosoftExcelAction` attributes

| Attribute | Description |
| --------- | ----------- |
| `spreadsheet_url` | Default workbook: OneDrive item id or sharing URL containing `/items/{id}` |
| `worksheet_title` | Default sheet tab name when a range omits an explicit sheet |

## Workspace REST API (per action)

Each Microsoft package registers **`endpoints.py`** when the package is imported (same pattern as Google workspace actions). Route paths match the Google analogs under **`/actions/{action_id}/...`** so clients can reuse the same URLs for `action_id` values that point at Microsoft action instances.

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

**Excel** (`MicrosoftExcelAction`): workbook routes in `microsoft_excel_action/endpoints.py` cover **`delete`**, **`share`**, and **`create`** (aligned with shared workspace URLs). The action class also implements Graph workbook operations similar to Sheets (`read_spreadsheet`, `update_spreadsheet`, etc.); expose additional routes there if you need HTTP parity with `google_sheets_action/endpoints.py`.

Authenticated admin routes typically require **`auth=True`** and role **`admin`**; refer to OpenAPI for full query/body schemas.

## Agent wiring (`agent.yaml`)

Add the action packages your agent needs. Credentials come from environment; no client JSON is stored on the action node (unlike Google client secrets).

```yaml
- action: jvagent/microsoft_outlook_mail_action
- action: jvagent/microsoft_outlook_calendar_action
- action: jvagent/microsoft_onedrive_action
- action: jvagent/microsoft_excel_action
  context:
    spreadsheet_url: ""   # optional default workbook id/URL
    worksheet_title: Sheet1
```

After deploy, complete OAuth once per action instance via each action’s **`auth_url`**.

## Implementation notes

- Tokens live on **`MicrosoftToken`** nodes linked to the action; access tokens are refreshed with the stored refresh token when expired.
- `MicrosoftAction.graph_request` accepts relative Graph paths or full URLs.
- OneDrive uploads may send raw bytes to Graph with the appropriate `Content-Type`; folder creation uses a JSON body without file content.
