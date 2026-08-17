# Google Docs Action

Create, read, update, and comment on Google Docs via the Docs API (and Drive for copy/export). **Login is MCP OAuth** (`MCPOAuthToken`), not `GoogleToken` / `/api/google/{action_id}`.

There is no `@tool` skill for Docs. Other Google actions (Sheets, Gmail, Drive, Calendar) use the same MCP login with their own `?service=`.

## Requirements

- **Google Cloud project** with Docs API and Drive API enabled
- **OAuth 2.0 Client ID** (Web application) with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`
- **`GOOGLE_CLIENT_SECRETS_JSON`** in `.env` (path or JSON string)
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`google_workspace`)

## Create credentials

This is an **OAuth client JSON** (client id + secret). It is **not** a service-account key from [IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) (APIs & Services → Credentials). Do **not** use IAM & Admin → Service Accounts.
2. Select or create the Google Cloud project.
3. Enable **Google Docs API** and **Google Drive API**: [APIs & Services → Library](https://console.cloud.google.com/apis/library).
4. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose Internal or External as appropriate. If the app is in Testing, add the Google accounts that will sign in as test users.
5. Click **Create credentials** → **OAuth client ID**.
6. Application type: **Web application**. Name it (for example `jvagent-mcp`).
7. Under **Authorized redirect URIs**, add exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin). Do **not** use the old `/api/google/callback/` path.
8. Click **Create**. Download the JSON (client id + secret).
9. Put the file path or the JSON contents in `.env` as `GOOGLE_CLIENT_SECRETS_JSON`, and set `JVAGENT_PUBLIC_BASE_URL` (see below).
10. Authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=docs`.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
# Path to the downloaded OAuth client JSON:
GOOGLE_CLIENT_SECRETS_JSON=/absolute/path/to/client_secret.json
# Or paste the file contents as a single-line JSON string:
# GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"...","client_secret":"...","redirect_uris":["https://your-public-host.example/api/mcp/google_workspace/auth/callback"]}}
```

`redirect_uris` in the downloaded JSON / Cloud Console must be the **MCP** callback (`/api/mcp/google_workspace/auth/callback`), not `/api/google/callback/`.

## Configuration

| Attribute       | Description                                              | Required |
| --------------- | -------------------------------------------------------- | -------- |
| `output_format` | Preferred output: `google_doc` or `markdown`             | No       |
| `auth_url`      | Set on startup to `/api/mcp/google_workspace/auth?account=integral&service=docs` | —        |

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
        tools: ["manage_docs"]
        show_tools: false
        sandbox_mode: false

- action: jvagent/google_docs_action
  context:
    enabled: true
```

Authorize once at **`/api/mcp/google_workspace/auth?service=docs`**.

## Methods

Python methods on `GoogleDocsAction` (no `@tool` skill wrapper):

- `create_document(title)`
- `copy_template_document(template_document_id, title, folder_id=None)`
- `read_document(document_id)`
- `append_text(document_id, text)`
- `replace_document_body(document_id, content)`
- `insert_text_at(document_id, text, index)`
- `insert_comment` / `list_comments`
- `replace_text` / `replace_named_placeholders`
- `render_markdown_blocks`
- `export_pdf(document_id)`
- `batch_update(document_id, requests)`

## Endpoints

Admin REST handlers under `/actions/{action_id}/docs/...` authenticate with the MCP OAuth token.

### Authorization

1. Open `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=docs`.
2. Complete Google consent. The callback stores `MCPOAuthToken` and hydrates google-workspace-mcp XDG credential files.
3. Docs methods and the MCP stdio server both use that token. Re-auth if refresh fails.

### Create

```http
POST /api/actions/{action_id}/docs/create
```

Body: `title`.

### Read

```http
GET /api/actions/{action_id}/docs/read?document_id=1abc...
```

### Append

```http
POST /api/actions/{action_id}/docs/append
```

Body: `document_id`, `text`.

### Copy template

```http
POST /api/actions/{action_id}/docs/copy-template
```

Body: `template_document_id`, `title`, optional `folder_id`.

### Batch update

```http
POST /api/actions/{action_id}/docs/batch-update
```

Body: `document_id`, `requests` (Docs API request list).

### Export PDF

```http
GET /api/actions/{action_id}/docs/export-pdf?document_id=1abc...
```
