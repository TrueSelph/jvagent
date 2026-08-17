# Microsoft Excel Action

Read and update **Excel workbooks stored on OneDrive** using Microsoft Graph **Excel REST** (workbook session, ranges, worksheets). **Login is MCP OAuth** (`MCPOAuthToken`), not `MicrosoftToken` / `/api/microsoft/{action_id}`.

Workbooks must be reachable as **`/me/drive/items/{itemId}`**. Resolve the item id from a sharing URL (path segment `/items/{id}`), or pass the raw id.

Shared setup: [Microsoft actions README](../README.md). Scopes match OneDrive read/write: `offline_access`, `User.Read`, `Files.ReadWrite.All`.

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
5. Open **API permissions** → **Microsoft Graph** → **Delegated** permissions. Add `offline_access`, `User.Read`, and `Files.ReadWrite.All`. Grant admin consent if the tenant requires it.
6. From the app **Overview**, copy **Application (client) ID** and **Directory (tenant) ID**.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
MICROSOFT_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MICROSOFT_CLIENT_SECRET=your-client-secret
MICROSOFT_TENANT_ID=common
```

Then authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth?service=excel`.

## Action attributes

| Attribute | Description |
| --------- | ----------- |
| `spreadsheet_url` | Default workbook: OneDrive item id or URL containing `/items/...` |
| `worksheet_title` | Default sheet tab when a range omits an explicit sheet (default `Sheet1`) |

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

- action: jvagent/microsoft_excel_action
  context:
    spreadsheet_url: ""
    worksheet_title: Sheet1
```

Authorize at **`/api/mcp/microsoft_365/auth?service=excel`**.

## REST API

**Spreadsheet routes** are the same paths as Google Sheets (`google_sheets_action/endpoints.py`), under `/api/actions/{action_id}/`:

| Path suffix | Supported for Excel |
| ----------- | ------------------- |
| `read` | Yes — `usedRange` or named A1 range |
| `update` | Yes — PATCH range with `values` (workbook session) |
| `append` | Yes — appends rows after used range |
| `clear` | Yes — clears listed ranges |
| `create` | Yes — creates new `.xlsx` under drive root via upload |
| `share` | Yes — link / user invite on the workbook item |
| DELETE (delete spreadsheet) | Yes — `spreadsheet_id` / `spreadsheet_url` |
| `worksheet/create` | Yes |
| `worksheet/update` | **Rename only** — body must include `new_title`; grid size / hidden / tab color not supported |
| `worksheet/delete` | Yes |
| `format` | **Not implemented** — calls raise `NotImplementedError` |
| `merge` / `unmerge` | **Not implemented** — same |

Use OpenAPI for full query/body fields (`spreadsheet_id`, `spreadsheet_url`, `range_name`, `worksheet_title`, etc.).

## Limitations vs Google Sheets

Graph Excel endpoints do not implement merge/unmerge or arbitrary cell formatting in this action; use values-only **update** / **clear** instead. **Worksheet update** is limited to renaming the tab (`new_title`).

## Workbook resolution

`resolve_workbook_item_id` accepts:

- A Graph URL containing `/items/{id}`
- A raw drive item id
- A Google Sheets URL is detected and treated as a Sheets id (for parity helpers)—for Excel you should pass a OneDrive-backed workbook
