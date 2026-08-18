# Microsoft OneDrive Action

Read and write the signed-in user’s **OneDrive** (default drive) via **Microsoft Graph** (`/me/drive/...`). **Login is MCP OAuth** (`MCPOAuthToken`), not `MicrosoftToken` / `/api/microsoft/{action_id}`.

Shared OAuth and env vars: [Microsoft actions README](../README.md).

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
# Optional default folder for list/upload:
# ONEDRIVE_PARENT_FOLDER_ID=root
```

Then authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth?service=onedrive`.

## Microsoft Graph scopes

Delegated scopes:

- `offline_access`
- `User.Read`
- `Files.ReadWrite.All`

## Environment (optional)

| Variable | Purpose |
| -------- | ------- |
| `ONEDRIVE_PARENT_FOLDER_ID` | Default folder for list/upload when `folder_id` / `parent_folder_id` is omitted: `root` or a drive **item** id |

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

- action: jvagent/microsoft_onedrive_action
```

Authorize at **`/api/mcp/microsoft_365/auth?service=onedrive`**.

## REST API (unified drive)

Paths assume `/api` prefix. Admin-authenticated; see OpenAPI for bodies.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/actions/{action_id}/list` | Recursive file tree under `folder_id` (default from `ONEDRIVE_PARENT_FOLDER_ID` or `root`): `with_link` for `webUrl` |
| POST | `/api/actions/{action_id}/upload` | Upload file or create folder: `name`; for files add `content` (base64) or `source_url`, optional `mime_type`, `parent_folder_id` |
| DELETE | `/api/actions/{action_id}/delete` | `file_id` — removes drive item |
| POST | `/api/actions/{action_id}/share` | `file_id`, `share_type` (`link` or `user`), `link_scope`, `email`, `role` — uses `createLink` / `invite` |
| POST | `/api/actions/{action_id}/compare_files` | Diff two nested listings (`added` / `removed` / `modified`) |

## Behavior notes

- **Folder create**: Call upload with `name` only (no `content` / `source_url`); creates a folder with rename-on-conflict.
- **File upload**: PUT to `/{parent}:/{filename}:/content` with decoded bytes from base64 `content` or bytes fetched from `source_url`.
- **Folders** in list output use MIME type `application/vnd.microsoft.graph.folder`.
